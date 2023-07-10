#!/usr/bin/env python3

import sys, os, logging, traceback, errno, argparse, subprocess, yaml, time
from datetime import datetime

import requests

'''
Classes
'''

class AgentBase:

    name = None
    config = None
    logger = None

    def __init__(self, config, name=None):
        '''config: dict, name: str = None'''
        self.name = name
        self.config = AgentBase.AgentConfig(config)
        if self.config.log.logger is None:
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(self.config.log.level)
            self.logger.addHandler(logging.StreamHandler())
            self.config.log.logger = self.logger
        else:
            self.logger = self.config.log.logger

    def request(self, *args, **kwargs):
        '''any -> Response | None'''
        try:
            return requests.post(*args, **kwargs)
        except Exception as e:
            tb = traceback.format_exc()
            self.logger.error(tb)
            response = requests.Response()
            response._content = bytes(str(e), 'utf-8')
            response.status_code = 0
            return response

    class AgentConfig(dict):

        def __init__(self, config):
            '''config: dict'''
            super(AgentBase.AgentConfig, self).__init__(config)
            for k, v in config.items():
                self.__setattr__(k, v)

        def __setitem__(self, key, value):
            '''key: str, value: Any -> None'''
            super(AgentBase.AgentConfig, self).__setitem__(key, value)
            self.__setattr__(key, value)

        def __delitem__(self, key):
            '''key: str -> None'''
            super(AgentBase.AgentConfig, self).__delitem__(key)
            delattr(self, key)

        def __setattr__(self, name, value):
            '''name: str, value: Any -> None'''
            if isinstance(value, dict):
                super(AgentBase.AgentConfig, self).__setattr__(name, AgentBase.AgentConfig(value))
            else:
                super(AgentBase.AgentConfig, self).__setattr__(name, value)

        def __delattr__(self, name):
            '''name: str -> None'''
            super(AgentBase.AgentConfig, self).__delattr__(name)
            del self[name]

class AgentPluginBase(AgentBase):

    def __init__(self, config, framework, name=None):
        '''config: dict, framework: Framewok, name: str = None'''
        super(AgentPluginBase, self).__init__(config, name=name)
        self.F = framework

    def get_plugin(self, name):
        '''name: str -> PluginBase'''
        return self.F.PluginManager.get_plugin_instance(name)

    def get_module(self, plugin, module):
        '''plugin: str, module: str -> PluginModuleBase'''
        plugin_instance = self.get_plugin(plugin)
        return plugin_instance.logic.get_module(module)

class AgentPlexmate(AgentPluginBase):

    def __init__(self, config, framework):
        '''config: dict, framework: Framework'''
        super(AgentPlexmate, self).__init__(config, framework, name='agent.plexmate')

    def get_scan_items(self, status):
        '''status: str -> list[ModelScanItem]'''
        return self.get_scan_model().get_list_by_status(status)

    def get_scan_targets(self, status):
        '''status: str -> dict[str, str]'''
        scan_items = self.get_scan_items(status)
        targets = {}
        for scan_item in scan_items:
            self.logger.debug(f'대상: {scan_item.target}')
            folder, file = os.path.split(scan_item.target)
            targets.setdefault(folder, []).append(file)
        return targets

    def get_module(self, module):
        return super(AgentPlexmate, self).get_module('plex_mate', module)

    def get_scan_model(self):
        '''None -> ModelScanItem'''
        return self.get_module('scan').web_list_model

    def check_scanning(self, max_scan_time):
        '''max_scan_time: int -> None'''
        '''
        SCANNING 항목 점검.

        PLEX_MATE에서 특정 폴더가 비정상적으로 계속 SCANNING 상태이면 이후 동일 폴더에 대한 스캔 요청이 모두 무시됨.
        예를 들어 .plexignore 가 있는 폴더는 PLEX_MATE 입장에서 스캔이 정상 종료되지 않기 때문에 해당 파일의 상태가 계속 SCANNING 으로 남게 됨.
        이 상태에서 동일한 폴더에 위치한 다른 파일이 스캔에 추가되면 스캔을 시도하지 않고 FINISH_ALREADY_IN_QUEUE 로 종료됨.

        ModelScanItem.queue_list가 현재 스캔중인 아이템의 MODEL 객체가 담겨있는 LIST임.
        클래스 변수라서 스크립트로 리스트의 내용을 조작해 보려고 시도했으나
        런타임 중 plex_mate.task_scan.Task.filecheck_thread_function() 에서 참조하는 ModelScanItem 과
        외부 스크립트에서 참조하는 ModelScanItem 의 메모리 주소가 다름을 확인함.
        flask의 app_context, celery의 Task 데코레이터, 다른 플러그인에서 접근을 해 보았지만 효과가 없었음.
        그래서 외부에서 접근한 ModelScanItem.queue_list는 항상 비어 있는 상태임.

        런타임 queue_list에서 스캔 오류 아이템을 제외시키기 위해 편법을 사용함.

        - 스캔 오류라고 판단된 item을 db에서 삭제하고 동일한 id로 새로운 item을 db에 생성
        - ModelScanItem.queue_list에는 기존 item의 객체가 아직 남아 있음.
        - 다음 파일체크 단계에서 queue_list에 남아있는 기존 item 정보로 인해 새로운 item의 STATUS가 FINISH_ALREADY_IN_QUEUE로 변경됨.
        - FINISH_* 상태가 되면 ModelScanItem.remove_in_queue()가 호출됨.
        - 새로운 item 객체는 기존 item 객체의 id를 가지고 있기 때문에 queue_list에서 기존 item 객체가 제외됨.

        주의: 계속 SCANNING 상태로 유지되는 항목은 확인 후 조치.
        '''
        model = self.get_scan_model()
        scans = self.get_scan_items('SCANNING')
        if scans:
            for scan in scans:
                if int((datetime.now() - scan.process_start_time).total_seconds() / 60) >= max_scan_time:
                    self.logger.warn(f'스캔 시간 {max_scan_time}분 초과: {scan.target}')
                    self.logger.warn(f'스캔 QUEUE에서 제외: {scan.target}')
                    model.delete_by_id(scan.id)
                    new_item = model(scan.target)
                    new_item.id = scan.id
                    new_item.save()

    def check_timeover(self, item_range):
        '''item_range: str -> None'''
        '''
        FINISH_TIMEOVER 항목 점검
        ID가 item_range 범위 안에 있는 TIMEOVER 항목들을 다시 READY 로 변경
        주의: 계속 시간 초과로 뜨는 항목은 확인 후 수동으로 조치
        '''
        overs = self.get_scan_items('FINISH_TIMEOVER')
        if overs:
            start_id, end_id = list(map(int, item_range.split('~')))
            for over in overs:
                if over.id in range(start_id, end_id):
                    self.logger.warn(f'READY 로 상태 변경 : {over.target}')
                    over.set_status('READY', save=True)

    def add_scan(self, target):
        '''target: str -> ModelScanItem'''
        scan_item = self.get_scan_model()(target)
        scan_item.save()
        self.logger.debug(f'added scan id: {scan_item.id}')
        return scan_item

class AgentRclone(AgentBase):

    connectible = False

    def __init__(self, config):
        '''config: dict'''
        super(AgentRclone, self).__init__(config, name='agent.rclone')
        self.connectible = self.check_connection()

    def check_connection(self):
        '''None -> bool'''
        response = self.command('core/version')
        if int(str(response.status_code)[0]) == 2:
            return True
        else:
            msg = f'접속 불가 CODE: {response.status_code}, 내용: {response.text}'
            self.logger.error(msg)
            return False

    def command(self, command, data=None):
        '''command: str, data: dict = None -> Response'''
        return self.request(
            f'{self.config.rclone.rc_addr}/{command}',
            auth=(self.config.rclone.rc_user, self.config.rclone.rc_pass),
            data=data
        )

    def _command(self, command, url, username=None, password=None):
        '''command: str, url: str, username: str = None, password: str = None -> Response'''
        return self.request(
            f'{url}/{command}',
            auth=(username if username else '', password if password else '')
        )

    def vfs_refresh(self, dirs):
        '''dirs: list[str] -> Response'''
        counter = 1
        data = {}
        for dir in dirs:
            #self.logger.debug(f'새로고침 경로: {dir}')
            data[f'dir{counter}'] = self.get_remote_path(dir)
            counter += 1
        return self.command('vfs/refresh', data=data)

    def get_remote_path(self, local_path):
        '''local_path: str -> str'''
        if os.path.isfile(local_path):
            local_path = os.path.split(local_path)[0]
        while not os.path.exists(local_path):
            if os.path.split(local_path)[1] == '':
                self.logger.error("마지막 경로까지 확인해 봤지만 존재하지 않는 경로예요.")
                raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), local_path)
            local_path = os.path.split(local_path)[0]
        for k, v in self.config.rclone.rc_mapping.items():
            local_path = os.path.normpath(local_path.replace(k, v))
        return local_path

class AgentInitBase(AgentBase):

    plugins_indtalled = None
    plugins_dir = None

    def __init__(self, config, name=None):
        '''config: dict, name: str = None'''
        super(AgentInitBase, self).__init__(config, name=name)
        with open(self.config.ff_config, 'r') as stream:
            self.config['ff_config'] = yaml.safe_load(stream)
            self.plugins_dir = f'{self.config.ff_config.path_data}/plugins'
        self.plugins_indtalled = self.get_installed_plugins()

    def get_installed_plugins(self):
        '''None -> dict[str, dict]'''
        plugins_indtalled = {}
        for dir in os.listdir(self.plugins_dir):
            try:
                info_file = f'{self.plugins_dir}/{dir}/info.yaml'
                with open(info_file, "r") as stream:
                    try:
                        plugins_indtalled[dir] = yaml.safe_load(stream)
                    except yaml.YAMLError as ye:
                        self.logger.error(ye)
            except FileNotFoundError as fnfe:
                self.logger.error(f'{fnfe}: {info_file}')
                continue
        return plugins_indtalled

    def check_command(self, *args):
        '''args: Unpack[str] -> bool'''
        return True if self.sub_run(*args).returncode == 0 else False

    def sub_run(self, *args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", **kwargs):
        '''args: Unpack[str], stdout: int = -1, stderr: int = -2, encoding: str = "utf-8", Unpack[Any, Any] -> CompletedProcess'''
        if not self.config.init.execute_commands:
            raise Exception(f'설정값에 의해 명령어를 실행할 수 없음 (execute_commands: {self.config.init.execute_commands})')
        try:
            return subprocess.run(args, stdout=stdout, stderr=stderr, encoding=encoding, **kwargs)
        except Exception as e:
            self.logger.error(e)
            return subprocess.CompletedProcess(args, returncode=1, stderr=str(e))

class AgentInitUbuntu(AgentInitBase):

    def __init__(self, config):
        '''config: dict'''
        super(AgentInitUbuntu, self).__init__(config, name='agent.init.ubuntu')

    def check_process(self, name, timeout):
        '''name: str, timeout: int -> bool'''
        counter = state = 0
        while state > -1:
            state = self.sub_run('ps', 'aux', timeout=timeout).stdout.find(name)
            if counter > timeout:
                break
            time.sleep(1)
            counter += 1
        return True if state < 0 else False

    def init(self):
        '''None -> None'''
        #self.check_process('apt', self.config.get('timeout', 300))
        #print(f'command : {self.check_command("nano", "-V")}')
        #test_plugins = ['sjva', 'klive_plus', 'wv_tool', 'wavve', 'kakaotv', 'cppl', 'bot_downloader', 'gds_tool', 'make_yaml', 'flaskfilemanager', 'terminal', 'ffmpeg', 'number_baseball', 'trans', 'static_host', 'rclone', 'support_site', 'metadata', 'discord_bot', 'libgdrive', 'fp_ktv', 'plex_mate', 'vnStat', 'torrent_info', 'tving_search', 'subtitle_tool', 'fp_movie', 'hotdeal_alarm', 'lotto', 'musicProc2', 'sporki', 'gugutv', 'narrtv', 'cooltv']

        require_plugins = {}
        require_packages = {}
        require_commands = {}

        # plugin by plugin
        for plugin, info in self.plugins_indtalled.items():
           # append this plugin's requires to
            depend_plugins = self.config.init.dependencies.get(plugin, {}).get('plugins', [])
            for depend in depend_plugins:
                require_plugins[depend] = False

            # update dependencies from info.yaml
            requires = info.get("require_plugin")
            if requires:
                for req in requires:
                    req = req.split("/")[-1]
                    require_plugins[req] = False

            # append this plugin's packages to
            for depend in self.config.init.dependencies.get(plugin, {}).get('packages', []):
                require_packages[depend] = False

            # append this plugin's commands to
            for depend in self.config.init.dependencies.get(plugin, {}).get('commands', []):
                require_commands[depend] = False

        # pop installed plugins
        for plugin in self.plugins_indtalled.keys():
            require_plugins.pop(plugin, None)

        executable_commands = []
        # 1. Commands from the config file
        if self.config.init.commands:
            for command in self.config.init.commands:
                executable_commands.append(command)

        # 2. Commands of installing required packages
        if require_packages:
            for req in require_packages.keys():
                command = f'apt-get install -y {req}'
                executable_commands.append(command)

        # 3. Commands from plugin dependencies of the config file
        if require_commands:
            for req in require_commands:
                executable_commands.append(req)

        # 4. Commands of installing required plugins
        if require_plugins:
            for plugin in require_plugins.keys():
                command = f'git clone {self.config.init.dependencies.get(plugin, {"repo": "NO INFO."}).get("repo")} {self.plugins_dir}/{plugin}'
                executable_commands.append(command)

        for command in executable_commands:
            self.logger.info(f'실행 예정 명령어: {command}')

        # run commands
        for command in executable_commands:
            result = self.sub_run("/usr/bin/env", "bash", "-c", command, timeout=self.config.init.timeout)
            if result.returncode == 0:
                msg = '성공'
            else:
                msg = result.stdout
            self.logger.info(f'실행 결과 [{command}]: {msg}')

'''
Methods
'''
def op_default(args, config):
    '''args: Namespace, config: dict -> None'''
    args.parser.print_help()

def op_plexmate(args, config):
    '''args: Namespace, config: dict -> None'''
    from framework.init_main import Framework # type: ignore
    F = Framework.get_instance()
    plexmate_agent = AgentPlexmate(config, F)
    def add_scan(dirs):
        for dir in dirs:
            plexmate_agent.add_scan(dir)
    if args.command == 'scan':
        '''
        ff-aider.py plexmate scan --dirs "/path/to/be/scanned"
        단순히 스캔만 실행할 경우 사용.
        이미 vfs/refresh 되어 있으나 스캔이 누락된 경우 등..
        '''
        add_scan(args.dirs)
    elif args.command == 'refresh':
        '''
        ff-aider.py plexmate refresh --dirs "/path/to/be/scanned"
        이미 존재하는 폴더를 vfs/refresh 한 뒤 스캔할 때 사용.
        이미 존재하는 폴더를 PLEX_MATE에 스캔 요청하면 파일 체크 주기에 따라서 vfs/refresh가 완료되기 전에 스캔이 실행 됨.
        vfs/refresh가 종료된 후 스캔을 추가할 필요가 있음.
        '''
        args.command = 'vfs/refresh'
        op_rclone(args, config)
        add_scan(args.dirs)
    elif args.command == 'periodic':
        '''
        ff-aider.py plexmate periodic {job id}
        주기적 스캔의 작업 ID 를 입력받아 vfs/refresh를 한 뒤 주기적 스캔을 실행
        '''
        mod = plexmate_agent.get_module('periodic')
        try:
            job = mod.get_jobs()[args.job_id]
        except IndexError as e:
            plexmate_agent.logger.error(e)
            job = None
        if job is not None:
            folder = job.get('폴더')
            args.dirs = []
            if folder is None:
                section = job.get('섹션ID')
                plex_db = plexmate_agent.get_plugin('plex_mate').PlexDBHandle
                for location in plex_db.section_location(library_id=section):
                    args.dirs.append(location.get('root_path'))
            else:
                args.dirs.append(folder)
            args.command = 'vfs/refresh'
            op_rclone(args, config)
            mod.one_execute(args.job_id)
    elif args.command is None:
        '''
        ff-aider.py plexmate
        command가 없을 경우 다음의 작업을 함
            - 스캐닝 시간을 초과한 항목을 처리
            - 파일 체크 TIMEOVER 항목을 처리
            - READY 상태의 항목을 vfs/refresh
        '''
        plexmate_agent.check_scanning(plexmate_agent.config.plexmate.max_scan_time)
        if hasattr(plexmate_agent.config.plexmate, 'timeover_range'):
            plexmate_agent.check_timeover(plexmate_agent.config.plexmate.timeover_range)
        args.dirs = list(plexmate_agent.get_scan_targets('READY').keys())
        args.command = 'vfs/refresh'
        op_rclone(args, config)

def op_rclone(args, config):
    '''args: Namespace, config: dict -> None'''
    rclone_agent = AgentRclone(config)
    if not rclone_agent.connectible:
        rclone_agent.logger.error(f'리모트에 접속할 수 없어요.')
    else:
        if args.command == 'vfs/refresh':
            '''
            ff-aider.py rclone vfs/refresh --dirs "/path/to/be/refresh"
            rclone 리모트에 vfs/refresh 명령을 전송
            '''
            if not args.dirs:
                rclone_agent.logger.info("새로고침 대상이 없어요.")
            else:
                response = rclone_agent.vfs_refresh(args.dirs)
                rclone_agent.logger.info(response.json())
        elif args.command is not None:
            '''
            ff-aider.py rclone {remote command}
            rclone 리모트에 {remote command} 명령을 전송
            '''
            print(rclone_agent.command(args.command).text)
        else:
            '''
            ff-aider.py rclone
            rclone 리모트에 options/get 명령을 전송
            '''
            print(rclone_agent.command('options/get').text)

def op_init(args, config):
    '''args: Namespace, config: dict -> None'''
    init_agent = AgentInitUbuntu(config)
    init_agent.init()

def op_test(args, config):
    '''args: Namespace, config: dict -> None'''
    print('test')

def run(config):
    '''config: dict [str, Any] -> None'''
    parser = argparse.ArgumentParser(
        description='Flaskfarm 간단(?) 스크립트',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.set_defaults(func=op_default, parser=parser)
    subparsers = parser.add_subparsers(title='Agents', dest='agent')

    arg_dirs = {
        'dest': 'dirs',
        'metavar': '"/path/to/be/refresh"',
        'action': 'extend',
        'default': [],
        'nargs': '+',
        'type': str,
        'help': '새로고침할 "로컬" 디렉토리: --dirs "/mnt/gds/A" "/mnt/gds/B"'
    }

    # for plexmate
    parser_plexmate = subparsers.add_parser(
        'plexmate',
        description='FF에서 LOAD 방식으로 실행해 주세요',
        help='PLEX MATE 관련 명령을 실행합니다'
    )
    parser_plexmate.set_defaults(func=op_plexmate)
    subparsers_plexmate = parser_plexmate.add_subparsers(title='Commands', dest='command', help='아무것도 입력하지 않으면 PLEX MATE의 READY 상태 아이템들을 vfs/refresh 합니다')

    # for plexmate scan
    parser_plexmate_scan = subparsers_plexmate.add_parser(
        'scan',
        help='PLEX MATE 플러그인에 스캔을 요청합니다'
    )
    parser_plexmate_scan.add_argument('--dirs', **arg_dirs)

    # for plexmate refresh
    parser_plexmate_refresh = subparsers_plexmate.add_parser(
        'refresh',
        help='Rclone 리모트에 vfs/refresh 를 요청한 후 PLEX MATE 에 스캔을 요청합니다'
    )
    parser_plexmate_refresh.add_argument('--dirs', **arg_dirs)

    # for plexmate periodic
    parser_plexmate_periodic = subparsers_plexmate.add_parser(
        'periodic',
        help='주기적 스캔의 작업 ID를 입력하여 vfs/refresh 후 해당 작업을 실행합니다'
    )
    parser_plexmate_periodic.add_argument('job_id', type=int)

    # for rclone
    parser_rclone = subparsers.add_parser(
        'rclone',
        description='rclone 리모트가 실행중이어야 합니다',
        help='rclone 리모트로 명령을 전송합니다'
    )
    parser_rclone.set_defaults(func=op_rclone)
    subparsers_rclone = parser_rclone.add_subparsers(title='Commands', dest='command', help='추가 파라미터가 필요한 명령은 vfs/refresh만 구현되어 있습니다. 파라미터가 필요없는 명령들(options/get)도 실행할 수 있습니다')

    # for rclone vfs/refresh
    parser_rclone_refresh = subparsers_rclone.add_parser(
        'vfs/refresh',
        description='rclone 리모트가 실행중이어야 합니다',
        help='rclone 리모트에 새로고침 명령을 전송합니다'
    )
    parser_rclone_refresh.add_argument('--dirs', **arg_dirs)

    # for init
    parser_init = subparsers.add_parser(
        'init',
        description='Flaskfarm 시작시 필요한 명령어를 실행할 때 사용합니다',
        help='Flaskfarm 시작시 실행되는 명령어 모음입니다'
    )
    parser_init.set_defaults(func=op_init)

    # for test
    parser_test = subparsers.add_parser(
        'test',
        description='테스트 부 명령어',
        help='테스트용'
    )
    parser_test.set_defaults(func=op_test)

    # finally
    args = parser.parse_args(config['args'])
    # strip leading/tailing double quote from dir string
    if hasattr(args, 'dirs'):
        for i, dir in enumerate(args.dirs):
            args.dirs[i] = dir.strip('"')
    args.func(args, config)

'''
Execution
'''

def main(*args, **kwargs):
    '''
    Flaskfarm에서 실행하면 호출되는 메소드

    :param *args: 실행 방식과 파일명 ex ['LOAD', '/data/commands/test.py', *args]
    :param **kwargs: 로거가 포함되어 있음 {'logger': <Logger command_x (LOGLEVEL)>}
    '''
    config_file = f'{os.path.dirname(os.path.abspath(__file__))}/ff_aider.yaml'
    config = None
    with open(config_file, "r") as stream:
        config = yaml.safe_load(stream)
    if config is None:
        raise Exception('설정 정보가 없어요.')
    if 'logger' in kwargs:
        config['log']['logger'] = kwargs['logger']
    else:
        config['log']['logger'] = None

    if args:
        args = list(args)
        args.pop(0)
    else:
        args = list(sys.argv)
    args.pop(0)

    config['args'] = args

    try:
        run(config)
    except Exception as e:
        print(traceback.format_exc())
        if config['log']['logger']:
            config['log']['logger'].error(traceback.format_exc())

    print('스크립트 완료, 로그를 확인해 주세요.')

if __name__ == "__main__":
    main()