import subprocess
import tempfile
import sys
import yaml

import defaults
from defaults import get_script


class AnsibleHelper(object):
    @staticmethod
    def run_playbook(playbook_file, vars_file, key_file, hosts_file=None):
        if hosts_file == None:
            # Default
            hosts_file = get_script('ansible/hosts')

        args = ['ansible-playbook', '-i', hosts_file,
                '--private-key', key_file,
                '--extra-vars', '@{0}'.format(vars_file), playbook_file]
        print ' '.join(args)
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output, error = process.communicate()
        
        if process.returncode != 0:
            print >> sys.stderr, ValueError     # TODO: change to use logger

        return process.returncode

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

    def init_cluster(self, cluster_name):
        pass

    def launch_nodes(self, num_nodes, instance_type):
        pass


class AWSCluster(Cluster):
      
    def _prepare_vars_dict(self):
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
                }

    def _make_vars_file(self, vars_dict):
        vars_file = tempfile.NamedTemporaryFile()
        vars_file.write(yaml.dump(vars_dict, default_flow_style=False))
        vars_file.flush()
        return vars_file

    def init_cluster(self, cluster_name):
        """
        Initialise security group(s), cluster controller etc
        """

        self._cluster_name = cluster_name
        vars_dict = self._prepare_vars_dict()

        print yaml.dump(vars_dict, default_flow_style=False)

        vars_file = self._make_vars_file(vars_dict)

        # Run ansible
        
        AnsibleHelper.run_playbook(get_script('ansible/init_01_create_sg.yml'),
                                   vars_file.name, self._config['key_file'])

        AnsibleHelper.run_playbook(get_script('ansible/init_01_create_s3_bucket.yml'),
                                   vars_file.name, self._config['key_file'])
        
        AnsibleHelper.run_playbook(get_script('ansible/init_02_create_controller.yml'),
                                   vars_file.name, self._config['key_file'])

        vars_file.close()


    def launch_nodes(self, num_nodes, instance_type):
        """
        Launch a group of application nodes of the same type
        """
        vars_dict = self._prepare_vars_dict()
        vars_dict['num_nodes'] = num_nodes
        vars_dict['instance_type'] = instance_type

        #print yaml.dump(vars_dict, default_flow_style=False)
        vars_file = self._make_vars_file(vars_dict)

        # Run ansible
        print 'Creating {0} nodes'.format(num_nodes)
        AnsibleHelper.run_playbook(get_script('ansible/nodes_01_create_nodes.yml'),
                                   vars_file.name, self._config['key_file'])
        #print 'Done'


        vars_file.close()



        