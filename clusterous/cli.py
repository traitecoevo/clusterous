import sys
import os
import defaults
import argparse
import logging
import textwrap

import clusterousmain
from clusterous import __version__, __prog_name__
from helpers import NoWorkingClusterException

class CLIParser(object):
    """
    Clusterous Command Line Interface
    """

    def __init__(self):
        pass

    def _configure_logging(self, level='INFO'):
        logging_dict = {
                            'version': 1,
                            'disable_existing_loggers': False,
                            'formatters': {
                                'standard': {
                                    'format': '%(message)s'
                                },
                            },
                            'handlers': {
                                'default': {
                                    'level': level,
                                    'class':'logging.StreamHandler',
                                },
                            },
                            'loggers': {
                                '': {
                                    'handlers': ['default'],
                                    'level': level,
                                    'propagate': True
                                },
                            }
                        }

        logging.config.dictConfig(logging_dict)

        # Disable logging for various libraries
        # TODO: is it possible to do this in a simpler way?
        libs = ['boto', 'paramiko', 'requests', 'marathon']
        for l in libs:
            logging.getLogger(l).setLevel(logging.WARNING)


    def _create_args(self, parser):
        parser.add_argument('--verbose', '-v', dest='verbose', action='store_true',
            default=False, help='Print verbose debug output')
        parser.add_argument('--version', action='version', version='%(prog)s {0}'.format(__version__))

    def _create_subparsers(self, parser):
        subparser = parser.add_subparsers(description='The following subcommands are available', dest='subcmd')

        start = subparser.add_parser('start', help='Create a new cluster based on profile file',
                                        description='Create a new cluster based on profile file')
        start.add_argument('profile_file', action='store')

        # Build Docker image
        build = subparser.add_parser('build-image', help='Build a new Docker image',
                                        description='Build a Docker image on the cluster from a Dockerfile')
        build.add_argument('dockerfile_folder', action='store', help='Local folder name which contains the Dockerfile')
        build.add_argument('image_name', action='store', help='Name of the docker image to be created on the cluster')

        # Docker image info
        image_info = subparser.add_parser('image-info', help='Get information on a Docker image',
                                            description='Get information about a Docker image on the cluster')
        image_info.add_argument('image_name', action='store', help='Name of the docker image available on the cluster')

        # Sync: put
        sync_put = subparser.add_parser('put', help='Copy a folder from local to the cluster')
        sync_put.add_argument('local_path', action='store', help='Path to the local folder')
        sync_put.add_argument('remote_path', action='store', help='Path on the shared volume', nargs='?', default='')

        # Sync: get
        sync_get = subparser.add_parser('get', help='Copy a folder from cluster to local')
        sync_get.add_argument('remote_path', action='store', help='Path on the shared volume')
        sync_get.add_argument('local_path', action='store', help='Path to the local folder')

        # ls
        ls = subparser.add_parser('ls', help='List contents of the shared volume')
        ls.add_argument('remote_path', action='store', help='Path on the shared volume', nargs='?', default='')

        # rm
        rm = subparser.add_parser('rm', help='Delete a folder on the shared volume')
        rm.add_argument('remote_path', action='store', help='Path on the shared volume')

        # workon
        workon = subparser.add_parser('workon', help='Set the working cluster',
                                        description='Set the currently active cluster by name')
        workon.add_argument('cluster_name', action='store', help='Name of the cluster')

        # Terminate
        terminate = subparser.add_parser('terminate', help='Terminate the working cluster',
                                            description='Terminate the working cluster, destroying all resources')
        terminate.add_argument('--confirm', dest='no_prompt', action='store_true',
            default=False, help='Immediately terminate cluster without prompting for confirmation')

        # Status
        cluster_status = subparser.add_parser('status', help='Status of the cluster',
                                                description='Show information on the state of the cluster and any running application')

        # Launch
        launch = subparser.add_parser('launch', help='Launch an environment from an environment file',
                                        description='Launch an environment on the cluster based on the specifications '
                                                    'in an environment file')
        launch.add_argument('environment_file', action='store')

        # Destroy
        destroy = subparser.add_parser('destroy', help='Stop the running application',
                                        description='Stops the running application containers on the'
                                        ' cluster and removes any SSH tunnels to the application')
        destroy.add_argument('--tunnel-only', dest='tunnel_only', action='store_true',
                            default=False, help='Only destroy any SSH tunnels, do not stop application')
        destroy.add_argument('--confirm', dest='no_prompt', action='store_true', default=False,
                                help='Immediately destroy application without prompting for confirmation')
        # Connect
        connect = subparser.add_parser('connect', help='Gets an interactive shell within a docker container',
                                description='Connects to a docker container and gets an interactive shell')
        connect.add_argument('component_name', action='store', help='Name of the component (see status command)')

    def _init_clusterous_object(self, args):
        app = None
        if args.verbose:
            self._configure_logging('DEBUG')
        else:
            self._configure_logging('INFO')

        app = clusterousmain.Clusterous()
        return app

    def _workon(self, args):
        app = self._init_clusterous_object(args)
        success, message = app.workon(cluster_name = args.cluster_name)
        print message
        return 0 if success else 1

    def _terminate_cluster(self, args):
        app = self._init_clusterous_object(args)
        cl = app.make_cluster_object()
        if not args.no_prompt:
            prompt_str = 'This will terminate the cluster {0}. Continue (y/n)? '.format(cl.cluster_name)
            cont = raw_input(prompt_str)
            if cont.lower() != 'y' and cont.lower() != 'yes':
                print 'Doing nothing'
                return 1

        app = self._init_clusterous_object(args)
        app.terminate_cluster()
        return 0

    def _launch_environment(self, args):
        app = self._init_clusterous_object(args)
        success, message = app.launch_environment(args.environment_file)

        if success and message:
            print '\nMessage for user:'
            print message

        return 0 if success else 1

    def _docker_image_info(self, args):
        app = self._init_clusterous_object(args)
        info = app.docker_image_info(args.image_name)
        if not info:
            print 'Docker image "{0}" does not exist in the Docker registry'.format(args.image_name)
            return 1
        else:
            print 'Docker image: {}:{}\nImage id: {}\nAuthor: {}\nCreated: {}'.format(
                info['image_name'], info['tag_name'], info['image_id'], info['author'], info['created'])
            return 0

    def _sync_put(self, args):
        app = self._init_clusterous_object(args)
        success, message = app.sync_put(local_path = args.local_path,
                                        remote_path = args.remote_path)
        if not success:
            print message
            return 1
        return 0

    def _sync_get(self, args):
        app = self._init_clusterous_object(args)
        success, message = app.sync_get(remote_path = args.remote_path,
                                        local_path = args.local_path)
        if not success:
            print message
            return 1
        return 0

    def _ls(self, args):
        app = self._init_clusterous_object(args)
        success, message = app.ls(remote_path = args.remote_path)
        print message
        return 0 if success else 1

    def _rm(self, args):
        app = self._init_clusterous_object(args)
        success, message = app.rm(remote_path = args.remote_path)
        print message
        return 0 if success else 1

    def _connect_to_container(self, args):
        app = self._init_clusterous_object(args)
        success, message = app.connect_to_container(component_name = args.component_name)
        if not success:
            print message
            return 1
        return 0

    def _cluster_status(self, args):
        app = self._init_clusterous_object(args)
        success, info = app.cluster_status()
        if not success:
            print info
            return 1

        # Formating
        up_time=info.get('cluster',{}).get('up_time')
        up_time_str = ''
        if up_time.years > 0: up_time_str += ' {0} years'.format(up_time.years)
        if up_time.months > 0: up_time_str += ' {0} months'.format(up_time.months)
        if up_time.days > 0: up_time_str += ' {0} days'.format(up_time.days)
        if up_time.hours > 0: up_time_str += ' {0} hours'.format(up_time.hours)
        if up_time.minutes > 0: up_time_str += ' {0} minutes'.format(up_time.minutes)
        if len(info.get('applications')) == 0:
            apps_str = '[No applications running]'
        else:
            apps_str = '\n'.join(['{0}: {1}'.format(k, str(v)) for k,v in info.get('applications').iteritems()])

        output_fmt = textwrap.dedent("""\
                        CLUSTER:
                        Name: {cluster_name}
                        Uptime: {up_time}
                        Controller IP: {controller_ip}

                        INSTANCES:
                        {instances}

                        APPLICATION COMPONENTS:
                        {applications}

                        SHARED VOLUME:
                        Total: {volume_total}
                        Used: {volume_used} ({volume_used_pct})
                        Free: {volume_free}\
                        """)
        output = output_fmt.format(cluster_name=info.get('cluster',{}).get('name'),
                       up_time=up_time_str,
                       controller_ip=info.get('cluster',{}).get('controller_ip'),
                       volume_total=info.get('volume',{}).get('total'),
                       volume_used=info.get('volume',{}).get('used'),
                       volume_used_pct=info.get('volume',{}).get('used_pct'),
                       volume_free=info.get('volume',{}).get('free'),
                       instances='\n'.join(['{0}: {1}'.format(k, str(v)) for k,v in info.get('instances').iteritems()]),
                       applications=apps_str,
                       )
        print output
        return 0

    def _destroy(self, args):
        # If the user specifies --tunnel-only, we don't prompt for confirmation
        if not args.no_prompt and not args.tunnel_only:
            cont = raw_input('This will destroy the running application. Continue (y/n)? ')
            if cont.lower() != 'y' and cont.lower() != 'yes':
                print 'Doing nothing'
                return 1

        app = self._init_clusterous_object(args)
        result = app.destroy_environment(args.tunnel_only)

        return 0 if result else 1

    def main(self, argv=None):
        parser = argparse.ArgumentParser(__prog_name__, description='Tool to create and manage compute clusters')

        self._create_args(parser)
        self._create_subparsers(parser)

        args = parser.parse_args(argv)

        status = 0
        try:
            if args.subcmd == 'start':
                app = self._init_clusterous_object(args)
                app.start_cluster(args)
            elif args.subcmd == 'terminate':
                self._terminate_cluster(args)
            elif args.subcmd == 'launch':
                self._launch_environment(args)
            elif args.subcmd == 'build-image':
                app = self._init_clusterous_object(args)
                app.docker_build_image(args)
            elif args.subcmd == 'image-info':
                app = self._init_clusterous_object(args)
                self._docker_image_info(args)
            elif args.subcmd == 'put':
                status = self._sync_put(args)
            elif args.subcmd == 'get':
                status = self._sync_get(args)
            elif args.subcmd == 'ls':
                status = self._ls(args)
            elif args.subcmd == 'rm':
                status = self._rm(args)
            elif args.subcmd == 'workon':
                status = self._workon(args)
            elif args.subcmd == 'status':
                status = self._cluster_status(args)
            elif args.subcmd == 'connect':
                status = self._connect_to_container(args)
            elif args.subcmd == 'destroy':
                status = self._destroy(args)
        # TODO: this exception should not be caught here
        except NoWorkingClusterException as e:
            pass

        return status

def main(argv=None):
    cli = CLIParser()
    return cli.main(argv)
