"""Test lab safeguards without touching a switch, changing routes, or sending media."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import testbed


def config_fixture():
    """Create example host assignments for validation; these are never applied."""
    config = json.loads((testbed.ROOT / "testbed/lld-black4.example.json").read_text())
    config["receiver"].update(interface="en7", ipv4="192.168.1.220")
    config["sender"].update(interface="eth0", ipv4="192.168.1.221")
    return config


def validated(config):
    """Exercise the same JSON loading path used by the command-line helper."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "test.json"
        path.write_text(json.dumps(config))
        return testbed.load_config(path)


class LabGuardTests(unittest.TestCase):
    """Keep invalid topology, dependency, and readiness claims out of lab runs."""

    def test_placeholders_cannot_launch(self):
        """Require assigned host values instead of copying diagram device IPs."""
        with self.assertRaises(ValueError):
            testbed.load_config(testbed.ROOT / "testbed/lld-black4.example.json")
        for address in ("192.168.1.75", "192.168.1.212", "127.0.0.1", "169.254.1.2"):
            config = config_fixture()
            config["receiver"]["ipv4"] = address
            with self.subTest(address=address), self.assertRaises(ValueError):
                validated(config)

    def test_controller_and_port_guards(self):
        """Reject mislabeled Prague runs, bad port ranges and duplicate hosts."""
        configs = []
        config = config_fixture()
        config["controller"] = "unsupported"
        configs.append(config)
        config = config_fixture()
        config["sender"]["udp_port_max"] = 49999
        configs.append(config)
        config = config_fixture()
        config["sender"]["ipv4"] = config["receiver"]["ipv4"]
        configs.append(config)
        for config in configs:
            with self.subTest(config=config), self.assertRaises(ValueError):
                validated(config)

    def test_command_binds_address_and_ports(self):
        """Keep ICE and receiver signaling on the configured Ethernet address."""
        config = validated(config_fixture())
        for role in ("receiver", "sender"):
            command = testbed.endpoint_command(config, role, Path("/tmp/pion-l4s"))
            self.assertEqual(command[command.index("--local-ip") + 1], config[role]["ipv4"])
            self.assertEqual(command[command.index("--udp-port-min") + 1], "50000")
        command = testbed.endpoint_command(config, "receiver", Path("/tmp/pion-l4s"))
        self.assertEqual(command[command.index("--listen") + 1], "192.168.1.220:8080")

    def test_prague_requires_matching_ecn_receiver(self):
        """Prevent a Prague sender from connecting to the historical zero-ECN receiver."""
        config = config_fixture()
        config["controller"] = "prague"
        config = validated(config)
        health = {"service": "pion-l4s", "role": "receiver", "feedback": "ccfb",
                  "local_ip": config["receiver"]["ipv4"], "session_claimed": False}
        with self.assertRaisesRegex(ValueError, "ECN observation"):
            testbed.check_receiver_health(health, config)
        health.update(controller="prague", ecn_observation=True)
        testbed.check_receiver_health(health, config)
        for role in ("sender", "receiver"):
            cmd = testbed.endpoint_command(config, role, Path("/tmp/pion-l4s"))
            self.assertEqual(cmd[cmd.index("--controller") + 1], "prague")
        config["feedback"] = "twcc"
        with self.assertRaisesRegex(ValueError, "ccfb"):
            validated(config)

    def test_binary_must_use_both_personal_forks(self):
        """Reject stock modules and machine-local replacements in lab binaries."""
        info = "\n".join([
            "dep github.com/pion/interceptor v0.1.51",
            "=> github.com/altanai/interceptor v0.1.51 h1:example",
            "dep github.com/pion/ice/v4 v4.4.8",
            "=> github.com/altanai/ice/v4 v4.4.8 h1:example",
        ])
        self.assertEqual(len(testbed.verify_forks(info)), 2)
        for bad in (info.replace("altanai/ice", "pion/ice"),
                    info.replace("github.com/altanai/ice/v4 v4.4.8", "../ice (devel)")):
            with self.subTest(info=bad), self.assertRaises(ValueError):
                testbed.verify_forks(bad)

    def test_pacing_backend_and_old_binary_guard(self):
        """Require valid sender-only selection and reject an incompatible binary early."""
        config = config_fixture()
        config.update(controller="prague", prague_pacing_backend="linux-timerfd")
        config = validated(config)
        sender = testbed.endpoint_command(config, "sender", Path("/tmp/pion-l4s"))
        receiver = testbed.endpoint_command(config, "receiver", Path("/tmp/pion-l4s"))
        self.assertEqual(sender[sender.index("--prague-pacing-backend") + 1], "linux-timerfd")
        self.assertNotIn("--prague-pacing-backend", receiver)
        with mock.patch.object(testbed.subprocess, "run") as probe:
            probe.return_value = mock.Mock(returncode=0, stdout="", stderr="old binary help")
            with self.assertRaisesRegex(ValueError, "rebuild"):
                testbed.verify_pacing_capability(Path("/tmp/pion-l4s"), config, "sender")
            probe.return_value.stderr = "-prague-pacing-backend string"
            testbed.verify_pacing_capability(Path("/tmp/pion-l4s"), config, "sender")
        config["prague_pacing_backend"] = "unsupported"
        with self.assertRaises(ValueError):
            validated(config)
        config.update(controller="gcc", prague_pacing_backend="linux-timerfd")
        with self.assertRaises(ValueError):
            validated(config)

    def test_receiver_must_be_unused_and_match_feedback(self):
        """Catch a second session, wrong peer address, or CCFB/TWCC mismatch."""
        config = config_fixture()
        health = {"service": "pion-l4s", "role": "receiver", "feedback": "ccfb",
                  "local_ip": config["receiver"]["ipv4"], "session_claimed": False}
        testbed.check_receiver_health(health, config)
        for field, value in (("session_claimed", True), ("feedback", "twcc"), ("local_ip", "192.168.1.222")):
            changed = copy.deepcopy(health)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                testbed.check_receiver_health(changed, config)

    def test_wifi_route_fails_before_opening_sockets(self):
        """Stop when the peer route would escape through a different interface."""
        config = config_fixture()
        interface = {"ipv4_addresses": [config["receiver"]["ipv4"]]}
        with mock.patch.object(testbed, "inspect_interface", return_value=interface), \
                mock.patch.object(testbed, "inspect_route", return_value={"interface": "en0"}), \
                mock.patch.object(testbed.socket, "socket") as socket_mock:
            with self.assertRaisesRegex(ValueError, "route.*en0"):
                testbed.doctor(config, "receiver")
            socket_mock.assert_not_called()

    def test_linux_interface_state(self):
        """Parse Linux address data and reject a cable-down interface."""
        device = {"ifname": "eth0", "flags": ["UP"], "operstate": "UP",
                  "addr_info": [{"family": "inet", "local": "192.168.1.221"}]}
        with mock.patch.object(testbed.platform, "system", return_value="Linux"), \
                mock.patch.object(testbed.socket, "if_nameindex", return_value=[(1, "eth0")]), \
                mock.patch.object(testbed.Path, "exists", return_value=False), \
                mock.patch.object(testbed, "capture") as capture:
            capture.return_value = json.dumps([device])
            self.assertEqual(testbed.inspect_interface("eth0")["ipv4_addresses"], ["192.168.1.221"])
            device["operstate"] = "DOWN"
            capture.return_value = json.dumps([device])
            with self.assertRaisesRegex(ValueError, "inactive"):
                testbed.inspect_interface("eth0")


if __name__ == "__main__":
    unittest.main()
