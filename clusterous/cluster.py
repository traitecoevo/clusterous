import subprocess
import tempfile
import sys
import os
import yaml
import logging
import time
import json

import boto.ec2
import paramiko

import defaults
from defaults import get_script, get_remote_dir
from helpers import AnsibleHelper

# TODO: Move to another module as appropriate, as this is very general purpose
def retry_till_true(func, sleep_interval, timeout_secs=300):
    """
    Call func repeatedly, with an interval of sleep_interval, for up to
    timeout_secs seconds, until func returns true.

    Returns true if succesful, false if timeout occurs
    """
    success = True
    start_time = time.time()
    while not func():
        if time.time() >= start_time + timeout_secs:
            success = False
            break
        time.sleep(sleep_interval)

    return success

class Cluster(object):
    """
    Represents infrastrucure aspects of the cluster. Includes high level operations
    for setting up cluster controller, launching application nodes etc.

    Prepares cluster to a stage where applications can be run on it
    """
    def __init__(self, config):
        self._config = config
        self._cluster_name = None
        self._running = False
        self._logger = logging.getLogger()

    def _get_controller_ip(self):
        if not self._cluster_name:
            raise ValueError('No cluster name, was cluster not initialised?')
        ip = None
        ip_file = os.path.expanduser(defaults.current_controller_ip_file)
        if os.path.isfile(ip_file):
            f = open(ip_file, 'r')
            ip = f.read().strip()
            f.close
        else:
            raise ValueError('Cannot find controller IP: {0}'.format(ip_file))
        return ip

    def init_cluster(self, cluster_name):
        pass

    def launch_nodes(self, num_nodes, instance_type):
        pass

    # TODO: do this properly when orchestration is implemented
    def _create_controller_tunnel(self, remote_port, local_port, key_file):
        args = ['ssh', '-i', key_file, '-N', '-f',
                'root@{0}'.format(self._get_controller_ip()),
                '-L', '{0}:127.0.0.1:{1}'.format(remote_port, local_port)]
        subprocess.call(args)



class AWSCluster(Cluster):

    def _ec2_vars_dict(self):
        if not self._cluster_name:
            raise ValueError('No cluster name, was cluster not initialised?')
        return {
                'AWS_KEY': self._config['access_key_id'],
                'AWS_SECRET': self._config['secret_access_key'],
                'region': self._config['region'],
                'keypair': self._config['key_pair'],
                'vpc_id': self._config['vpc_id'],
                'vpc_subnet_id': self._config['subnet_id'],
                'cluster_name': self._cluster_name,
                'security_group_name': defaults.security_group_name_format.format(self._cluster_name),
                'controller_ami_id': defaults.controller_ami_id,
                'controller_instance_name': defaults.controller_name_format.format(self._cluster_name),
                'controller_instance_type': defaults.controller_instance_type,
                'node_name': defaults.node_name_format.format(self._cluster_name),
                'node_ami_id': defaults.node_ami_id,
                'registry_s3_bucket': defaults.registry_s3_bucket,
                'registry_s3_path': defaults.registry_s3_path,
                'current_controller_ip_file': defaults.current_controller_ip_file,
                'remote_scripts_dir': get_remote_dir(),
                'remote_host_scripts_dir': defaults.remote_host_scripts_dir
                }

    def _ansible_env_credentials(self):
        return {
                'AWS_ACCESS_KEY_ID': self._config['access_key_id'],
                'AWS_SECRET_ACCESS_KEY': self._config['secret_access_key']
                }

    def _run_remote_vars_dict(self):
        if not self._cluster_name:
            raise ValueError('No cluster name, was cluster not initialised?')
        return {
                'controller_ip': self._get_controller_ip(),
                'key_file_src': self._config['key_file'],
                'key_file_name': defaults.remote_host_key_file,
                'vars_file_src': None,  # must be filled in
                'vars_file_name': defaults.remote_host_vars_file,
                'remote_dir': defaults.remote_host_scripts_dir,
                'playbook_file': None
                }

    def _make_vars_file(self, vars_dict):
        vars_file = tempfile.NamedTemporaryFile()
        vars_file.write(yaml.dump(vars_dict, default_flow_style=False))
        vars_file.flush()
        return vars_file

    def _run_remote(self, vars_dict, playbook):

        vars_file = self._make_vars_file(vars_dict)

        local_vars = self._run_remote_vars_dict()
        local_vars['vars_file_src'] = vars_file.name
        local_vars['playbook_file'] = playbook

        local_vars_file = self._make_vars_file(local_vars)

        AnsibleHelper.run_playbook(get_script('ansible/run_remote.yml'),
                      local_vars_file.name, self._config['key_file'],
                      hosts_file=os.path.expanduser(defaults.current_controller_ip_file))

        local_vars_file.close()
        vars_file.close()


    def init_cluster(self, cluster_name):
        """
        Initialise security group(s), cluster controller etc
        """
        self._cluster_name = cluster_name
        vars_dict = self._ec2_vars_dict()

        vars_file = self._make_vars_file(vars_dict)

        # Run ansible

        # Due to a possible bug (Ansible=1.9.1), we apparently need to specify
        # AWS keys in a special environment variable
        env = self._ansible_env_credentials()
        self._logger.debug('Creating security group')
        AnsibleHelper.run_playbook(get_script('ansible/init_01_create_sg.yml'),
                                   vars_file.name, self._config['key_file'],
                                   env=env)

        self._logger.debug('Ensuring Docker registry bucket exists')
        AnsibleHelper.run_playbook(get_script('ansible/init_02_create_s3_bucket.yml'),
                                   vars_file.name, self._config['key_file'],
                                   env=env)

        self._logger.debug('Creating and configuring controller instance...')
        AnsibleHelper.run_playbook(get_script('ansible/init_03_create_controller.yml'),
                                   vars_file.name, self._config['key_file'],
                                   env=env)
        self._logger.debug('Launched controller instance at {0}'.format(self._get_controller_ip()))

        # TODO: do this properly when orchestration is implemented
        self._create_controller_tunnel(8080, 8080, os.path.expanduser(self._config['key_file']))

        vars_file.close()


    def launch_nodes(self, num_nodes, instance_type, node_tag):
        """
        Launch a group of application nodes of the same type.
        node_name is the Mesos tag by which the application can find a node
        """
        vars_dict = self._ec2_vars_dict()
        vars_dict['num_nodes'] = num_nodes
        vars_dict['instance_type'] = instance_type
        vars_dict['node_tag'] = node_tag

        self._logger.debug('Adding {0} nodes to cluster...'.format(num_nodes))
        self._run_remote(vars_dict, 'create_nodes.yml')


    def docker_build_image(self, args):
        """
        Create a new docker image
        """
        try:
            full_path = args.dockerfile_folder
            if args.dockerfile_folder.startswith('./'):
                full_path = os.path.abspath(args.dockerfile_folder)

            if not os.path.isdir(full_path):
                self._logger.error("Error: Folder '{0}' does not exists.".format(full_path))
                return

            if not os.path.exists("{0}/Dockerfile".format(full_path)):
                self._logger.error("Error: Folder '{0}' does not have a Dockerfile.".format(full_path))
                return

            self._cluster_name = args.cluster_name
            vars_dict={
                    'cluster_name': args.cluster_name,
                    'dockerfile_path': os.path.dirname(full_path),
                    'dockerfile_folder': os.path.basename(full_path),
                    'image_name':args.image_name,
                    }
            vars_file = self._make_vars_file(vars_dict)
            self._logger.info('Started building docker image')
            AnsibleHelper.run_playbook(defaults.get_script('ansible/docker_01_build_image.yml'),
                                       vars_file.name,
                                       self._config['key_file'],
                                       env=self._ansible_env_credentials(),
                                       hosts_file=os.path.expanduser(defaults.current_controller_ip_file))
            vars_file.close()
            self._logger.info('Finished building docker image')
        except Exception as e:
            self._logger.error(e)
            raise

    def docker_image_info(self, args):
        """
        Gets information of a Docker image
        """
        try:
            self._cluster_name = args.cluster_name
            
            if ':' in args.image_name:
                image_name, tag_name = args.image_name.split(':')
            else:
                image_name = args.image_name
                tag_name = 'latest'

            with paramiko.SSHClient() as ssh:
                logging.getLogger("paramiko").setLevel(logging.WARNING)
                ssh.load_system_host_keys()
                ssh.connect(hostname = self._get_controller_ip(), username = 'root', key_filename = os.path.expanduser(self._config['key_file']))
    
                # get image_id
                cmd = 'curl registry:5000/v1/repositories/library/{0}/tags/{1}'.format(image_name, tag_name)
                stdin, stdout, stderr = ssh.exec_command(cmd)
                image_id = stdout.read().replace('"','')
                if 'Tag not found' in image_id:
                    self._logger.info('"{0}" docker image does not exist.'.format(args.image_name))
                    return
    
                # get image_info
                cmd = 'curl registry:5000/v1/images/{0}/json'.format(image_id)
                stdin, stdout, stderr = ssh.exec_command(cmd)
                json_results = json.loads(stdout.read())
                self._logger.info('Docker image: {}:{}\nImage id: {}\nAuthor: {}\nCreated: {}\n'.format(
                    image_name, tag_name, image_id, json_results.get('author',''), json_results.get('created','')))

        except Exception as e:
            self._logger.error(e)
            raise

    def terminate_cluster(self, cluster_name):
        conn = boto.ec2.connect_to_region(self._config['region'],
                    aws_access_key_id=self._config['access_key_id'],
                    aws_secret_access_key=self._config['secret_access_key'])

        # Delete instances
        instance_filters = { 'tag:Name':
                        ['{0}_controller'.format(cluster_name),
                        '{0}_node'.format(cluster_name)],
                        'instance-state-name': ['running', 'pending']
                        }
        instance_list = conn.get_only_instances(filters=instance_filters)
        num_instances = len(instance_list)
        instances = [ i.id for i in instance_list ]

        self._logger.info('Terminating {0} instances'.format(num_instances))
        conn.terminate_instances(instance_ids=instances)

        def instances_terminated():
            term_filter = {'instance-state-name': 'terminated'}
            num_terminated = len(conn.get_only_instances(instance_ids=instances, filters=term_filter))
            return num_terminated == num_instances

        success = retry_till_true(instances_terminated, 2)
        if not success:
            self._logger.error('Timeout while trying to terminate instances in {0}'.format(cluster_name))
        else:
            self._logger.debug('{0} instances terminated'.format(num_instances))

        # Delete EBS volume
        volumes = conn.get_all_volumes(filters={'tag:Name':cluster_name})
        volumes_deleted = [ v.delete() for v in volumes ]
        volume_ids_str = ','.join([ v.id for v in volumes])
        if False in volumes_deleted:
            self._logger.error('Unable to delete volume in {0}: {1}'.format(cluster_name, volume_ids_str))
        else:
            self._logger.debug('Deleted shared volume: {0}'.format(volume_ids_str))

        # Delete security group
        sg = conn.get_all_security_groups(filters={'tag:Name':'{0}-sg'.format(cluster_name)})
        sg_deleted = [ g.delete() for g in sg ]
        if False in sg_deleted:
            self._logger.error('Unable to delete security group for {0}'.format(cluster_name))
        else:
            self._logger.debug('Deleted security group')

        return True