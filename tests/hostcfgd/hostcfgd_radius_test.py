import importlib.machinery
import importlib.util
import filecmp
import shutil
import os
import sys
from swsscommon import swsscommon

from parameterized import parameterized
from unittest import TestCase, mock
from tests.hostcfgd.test_radius_vectors import HOSTCFGD_TEST_RADIUS_VECTOR
from tests.common.mock_configdb import MockConfigDb, MockDBConnector
from sonic_py_common.general import getstatusoutput_noshell


test_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
modules_path = os.path.dirname(test_path)
scripts_path = os.path.join(modules_path, "scripts")
templates_path = os.path.join(modules_path, "data/templates")
output_path = os.path.join(test_path, "hostcfgd/output")
sample_output_path = os.path.join(test_path, "hostcfgd/sample_output")
sys.path.insert(0, modules_path)

# Load the file under test
hostcfgd_path = os.path.join(scripts_path, 'hostcfgd')
loader = importlib.machinery.SourceFileLoader('hostcfgd', hostcfgd_path)
spec = importlib.util.spec_from_loader(loader.name, loader)
hostcfgd = importlib.util.module_from_spec(spec)
loader.exec_module(hostcfgd)
sys.modules['hostcfgd'] = hostcfgd

# Mock swsscommon classes
hostcfgd.ConfigDBConnector = MockConfigDb
hostcfgd.DBConnector = MockDBConnector
hostcfgd.Table = mock.Mock()

class TestHostcfgdRADIUS(TestCase):
    """
        Test hostcfd daemon - RADIUS
    """
    def run_diff(self, file1, file2):
        _, output = getstatusoutput_noshell(['diff', '-ur', file1, file2])
        return output


    @parameterized.expand(HOSTCFGD_TEST_RADIUS_VECTOR)
    def test_hostcfgd_radius(self, test_name, test_data):
        """
            Test RADIUS hostcfd daemon initialization
            Args:
                test_name(str): test name
                test_data(dict): test data which contains initial Config Db tables, and expected results
            Returns:
                None
        """

        t_path = templates_path
        op_path = output_path + "/" + test_name
        sop_path = sample_output_path + "/" + test_name

        hostcfgd.PAM_AUTH_CONF_TEMPLATE = t_path + "/common-auth-sonic.j2"
        hostcfgd.NSS_TACPLUS_CONF_TEMPLATE = t_path + "/tacplus_nss.conf.j2"
        hostcfgd.NSS_RADIUS_CONF_TEMPLATE = t_path + "/radius_nss.conf.j2"
        hostcfgd.PAM_RADIUS_AUTH_CONF_TEMPLATE = t_path + "/pam_radius_auth.conf.j2"
        hostcfgd.PAM_AUTH_CONF = op_path + "/common-auth-sonic"
        hostcfgd.NSS_TACPLUS_CONF = op_path + "/tacplus_nss.conf"
        hostcfgd.NSS_RADIUS_CONF = op_path + "/radius_nss.conf"
        hostcfgd.NSS_CONF = op_path + "/nsswitch.conf"
        hostcfgd.ETC_PAMD_SSHD = op_path + "/sshd"
        hostcfgd.ETC_PAMD_LOGIN = op_path + "/login"
        hostcfgd.RADIUS_PAM_AUTH_CONF_DIR = op_path + "/"

        shutil.rmtree( op_path, ignore_errors=True)
        os.mkdir( op_path)

        shutil.copyfile( sop_path + "/sshd.old", op_path + "/sshd")
        shutil.copyfile( sop_path + "/login.old", op_path + "/login")

        MockConfigDb.set_config_db(test_data["config_db"])
        host_config_daemon = hostcfgd.HostConfigDaemon()

        aaa = host_config_daemon.config_db.get_table('AAA')

        try:
            radius_global = host_config_daemon.config_db.get_table('RADIUS')
        except:
            radius_global = []
        try:
            radius_server = \
                host_config_daemon.config_db.get_table('RADIUS_SERVER')
        except:
            radius_server = []

        host_config_daemon.aaacfg.load(aaa,[],[],radius_global,radius_server, {}, {})
        dcmp = filecmp.dircmp(sop_path, op_path)
        diff_output = ""
        for name in dcmp.diff_files:
            diff_output += \
                "Diff: file: {} expected: {} output: {}\n".format(\
                    name, dcmp.left, dcmp.right)
            diff_output += self.run_diff( dcmp.left + "/" + name,\
                dcmp.right + "/" + name)
        self.assertTrue(len(diff_output) == 0, diff_output)
