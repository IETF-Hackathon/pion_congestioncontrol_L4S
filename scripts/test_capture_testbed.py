"""Check capture identity and deployment boundaries without lab access or traffic."""
from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest import mock

import capture_testbed
import sender
import server
import source_manifest
from test_testbed import config_fixture


class CaptureTests(unittest.TestCase):
    """Keep planning offline and prevent ambiguous monitor/SSH targets."""
    def test_exact_result_and_origin(self):
        """Reject sibling run prefixes, off-origin links and traversal paths."""
        with tempfile.TemporaryDirectory() as folder:
            monitor=capture_testbed.Monitor('http://lld-black4.local/',Path(folder),'run-a')
            self.assertEqual(monitor.locate('<a href="run-a_2026_11_15_10_30/">ok</a>'),'/data/run-a_2026_11_15_10_30/')
            for href in ('run-ab_2026/','http://evil.local/data/run-a_2026/','/elsewhere/run-a_2026/','/data/../run-a_2026/'):
                self.assertIsNone(monitor.locate('<a href="'+href+'">bad</a>'))

    def test_target_validation(self):
        """Prevent option injection and broad remote-root deployments."""
        sender.validate_target('selabs@192.168.1.216','/home/selabs/pion-l4s-interop')
        for host,root in (('-oProxyCommand=bad','/tmp/project'),('host','/'),('host','/home/../tmp'),('host;bad','/tmp/project')):
            with self.assertRaises(ValueError):sender.validate_target(host,root)

    def test_snapshot_excludes_local_configs_and_downloads(self):
        """Avoid shipping local credentials, generated artifacts or unfinished downloads."""
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in ('run-testbed.sh','scripts/tool.py','testbed/load25m.local.json','Unconfirmed.crdownload','archive/private.txt'):
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('value')
            selected={str(relative) for _,relative in server.source_files(root)}
            self.assertEqual(selected,{'run-testbed.sh','scripts/tool.py'})

    def test_stale_binary_manifest_rejected(self):
        """Catch source edits after a build before a new capture can be reserved."""
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'cmd').mkdir()
            for name in ('go.mod','go.sum','cmd/main.go','binary'):(root/name).write_text('original')
            binary=root/'binary'
            Path(str(binary)+'.build.json').write_text(json.dumps(dict(binary_sha256=source_manifest.digest(binary),source_sha256=source_manifest.compiled_sources(root))))
            source_manifest.verify_build(binary,root)
            (root/'cmd/main.go').write_text('changed')
            with self.assertRaisesRegex(ValueError,'changed since build'):source_manifest.verify_build(binary,root)

    def test_ambiguous_capture_start_is_not_retried(self):
        """On a start timeout, clean up the owned receiver and never launch a sender."""
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'testbed').mkdir();(root/'testbed/config.json').write_text('{}')
            args=SimpleNamespace(run_id='guard-test',binary='bin/pion-l4s',config='testbed/config.json',
                host='host',remote_root='/home/project',capture_seconds=60,go_bin=None,ssh_config='/dev/null',control_path=None,collect_pcaps=False)
            config=config_fixture();config['duration_seconds']=30
            config['bottleneck']['dashboard_url']='http://lld-black4.local/'
            source={'sha256':'same'};build={'binary_sha256':'binary','source_sha256':{}}
            process=mock.Mock();process.poll.return_value=None;process.wait.return_value=0
            def get(path,filename=None,limit=0):
                """Simulate an ambiguous mutation result after successful read-only preflight."""
                if path.startswith('/ui/start_test'):raise TimeoutError('ambiguous capture response')
                return ''
            health={'service':'pion-l4s','role':'receiver','feedback':'ccfb','controller':config['controller'],
                    'local_ip':config['receiver']['ipv4'],'session_claimed':False}
            with mock.patch.object(capture_testbed,'ROOT',root), \
                 mock.patch.object(source_manifest,'verify_build',return_value=build), \
                 mock.patch.object(capture_testbed.subprocess,'run',return_value=mock.Mock(returncode=1)), \
                 mock.patch.object(capture_testbed.subprocess,'Popen',return_value=process) as popen, \
                 mock.patch.object(sender,'remote_command',side_effect=[json.dumps(dict(build=build,source=source)),'','{}']), \
                 mock.patch.object(capture_testbed.Monitor,'get',side_effect=get) as monitor_get, \
                 mock.patch.object(capture_testbed.Monitor,'status',return_value={'idle':True,'active':False}), \
                 mock.patch.object(capture_testbed.Monitor,'locate',return_value=None), \
                 mock.patch.object(capture_testbed.testbed,'http_json',return_value=health):
                with self.assertRaises(TimeoutError):capture_testbed.run(args,config,source)
            self.assertEqual(sum(call.args[0].startswith('/ui/start_test') for call in monitor_get.call_args_list),1)
            self.assertEqual(popen.call_count,1)
            process.send_signal.assert_called_once()
            manifest=json.loads((root/'results/raw/guard-test/monitor/manifest.json').read_text())
            self.assertEqual(manifest['capture_start_attempts'],1)
            self.assertTrue(manifest['original_config_preserved'])


if __name__=='__main__':unittest.main()
