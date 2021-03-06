#!/usr/bin/env python2

# Copyright 2013-present Barefoot Networks, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import subprocess
import signal
import os
import sys
import json
import time
import argparse
from sourcer import sourceFile

env_vars = sourceFile('./env.sh')
sys.path.append(os.path.join(env_vars['BMV2_PATH'], 'mininet'))

from mininet.net import Mininet
from mininet.topo import Topo
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI

from p4_mininet import P4Switch, P4Host

sys.path.append('..')
from pygotthard import GOTTHARD_MAX_OP
from time import sleep
from util import waitForTcpPort

parser = argparse.ArgumentParser(description='Mininet demo')
parser.add_argument('--behavioral-exe', help='Path to behavioral executable',
                    type=str, action="store", required=True)
parser.add_argument('--thrift-port', help='Thrift server port for table updates',
                    type=int, action="store", default=9090)
parser.add_argument('--num-clients', help='Number of hosts to connect to switch',
                    type=int, action="store", default=2)
parser.add_argument('--lmode', choices=['l2', 'l3'], type=str, default='l3')
parser.add_argument('--cli', help="start the mininet cli",
                    action="store_true", required=False, default=False)
parser.add_argument("--mode", "-m", choices=['forward', 'early_abort', 'optimistic_abort'], type=str, default="early_abort")
parser.add_argument('--json', help='Path to JSON config file',
                    type=str, action="store", required=True)
parser.add_argument('--pcap-dump', help='Dump packets on interfaces to pcap files',
                    action="store_true", required=False, default=False)
parser.add_argument('--server-delay', help='Delay (ms) between switch and server',
                    type=int, action="store", required=False, default=0)
parser.add_argument('--client-delay', help='Delay (ms) between switch and client',
                    type=int, action="store", required=False, default=0)
parser.add_argument('--client-cmd', help='Command to execute on clients',
                    type=str, action="store", required=False, default=False)
parser.add_argument('--server-cmd', help='Command to start server',
                    type=str, action="store", required=False, default=False)
parser.add_argument('--config', help='JSON client config file',
                    type=str, action="store", required=False, default=False)
parser.add_argument('--entries', help='default table entries (commands.txt) to add to the switch',
                    type=str, action="store", required=False, default='default_commands.txt')

args = parser.parse_args()


class SingleSwitchTopo(Topo):
    "Single switch connected to n (< 256) hosts."
    def __init__(self, sw_path, json_path, thrift_port, pcap_dump, hosts, **opts):
        # Initialize topology and default options
        Topo.__init__(self, **opts)

        switch = self.addSwitch('s1',
                                sw_path = sw_path,
                                json_path = json_path,
                                thrift_port = thrift_port,
                                pcap_dump = pcap_dump)

        for h in hosts:
            host = self.addHost(h['name'], ip=h['ip']+'/24', mac=h['mac'])
            self.addLink(host, switch, delay="%dms"%h['delay'])


def fmtStr(tmpl, params):
    return reduce(lambda s, p: s.replace('%'+p[0]+'%', str(p[1])), params.items(), tmpl)

def main():
    if args.config:
        with open(args.config, 'r') as f:
            conf = json.load(f)
        assert('server' in conf and type(conf['server']) is dict)
        assert('cmd' in conf['server'])
        assert('clients' in conf and type(conf['clients']) is list)
    else:
        conf = dict(server=dict(cmd=args.server_cmd),
                    clients=[dict(cmd=args.client_cmd) for _ in xrange(args.num_clients)])

    if not 'switch' in conf: conf['switch'] = dict(mode=args.mode)
    if not 'sequential_clients' in conf: conf['sequential_clients'] = False

    conf['dir'] = os.path.dirname(os.path.abspath(args.config if args.config else './'))
    conf['log_dir'] = os.path.join(conf['dir'], 'logs')
    if not os.path.isdir(conf['log_dir']):
        if os.path.exists(conf['log_dir']): raise Exception('Log dir exists and is not a dir')
        os.mkdir(conf['log_dir'])

    hosts = []
    srv = conf['server']

    params = dict(conf['parameters'].items() if 'parameters' in conf else [])

    server_addr = srv['addr'] if 'addr' in srv else "10.0.0.10"
    server_port = srv['port'] if 'port' in srv else "9999"


    server_log = os.path.join(conf['log_dir'], 'server.log')
    if os.path.exists(server_log): os.remove(server_log)
    hosts.append(dict(
            name = srv['name'] if 'name' in srv else 'h1',
            ip = srv['ip'] if 'ip' in srv else "10.0.0.10",
            sw_addr = srv['sw_addr'] if 'sw_addr' in srv else "10.0.0.1",
            mac = srv['mac'] if 'mac' in srv else '00:04:00:00:00:00',
            sw_mac = srv['sw_mac'] if 'sw_mac' in srv else "00:aa:bb:00:00:00",
            delay = srv['delay'] if 'delay' in srv else args.server_delay,
            cmd = fmtStr(srv['cmd'].replace('%h', server_addr).replace('%p', server_port).replace('%l', server_log), params)
            ))
    for n, cl in enumerate(conf['clients']):
        assert(type(cl) is dict and 'cmd' in cl)
        h = n + 1
        host = dict(
                name = cl['name'] if 'name' in cl else 'h%d' % (h + 1),
                ip = cl['ip'] if 'ip' in cl else "10.0.%d.10" % h,
                sw_addr = cl['sw_addr'] if 'sw_addr' in cl else "10.0.%d.1" % h,
                mac = cl['mac'] if 'mac' in cl else '00:04:00:00:00:%02x' % h,
                sw_mac = cl['sw_mac'] if 'sw_mac' in cl else "00:aa:bb:00:00:%02x" % h,
                delay = cl['delay'] if 'delay' in cl else args.client_delay)
        if 'stdout_log' in cl and cl['stdout_log']:
            host['stdout_log'] = os.path.join(conf['log_dir'], '%s.stdout.log' % host['name'])
        host['log'] = os.path.join(conf['log_dir'], '%s.log' % host['name'])
        if os.path.exists(host['log']): os.remove(host['log'])
        host['cmd'] = cl['cmd'].replace('%h', server_addr).replace('%p', server_port).replace('%e', conf['dir']).replace('%l', host['log'])
        host['cmd'] = fmtStr(host['cmd'], params)
        hosts.append(host)

    topo = SingleSwitchTopo(args.behavioral_exe,
                            args.json,
                            args.thrift_port,
                            args.pcap_dump,
                            hosts)
    net = Mininet(topo = topo,
                  link = TCLink,
                  host = P4Host,
                  switch = P4Switch,
                  controller = None)
    net.start()



    for n, host in enumerate(hosts):
        h = net.get(host['name'])
        if args.lmode == "l2":
            h.setDefaultRoute("dev eth0")
        else:
            h.setARP(host['sw_addr'], host['sw_mac'])
            h.setDefaultRoute("dev eth0 via %s" % host['sw_addr'])

    for host in hosts:
        h = net.get(host['name'])
        h.describe()


    waitForTcpPort(9090, timeout=120) # wait for P4 switch to start thrift server
    sleep(0.3)

    with open(args.entries, 'r') as f:
        t_entries = [l.rstrip() for l in f.readlines() if l != '\n']

    max_op_cnt = GOTTHARD_MAX_OP
    if conf['switch']['mode'] != 'forward': # i.e. both early/opti abort
        read_cache_enabled = 1 if conf['switch']['mode'] == 'read_cache' else 0
        opti_enabled = 1 if conf['switch']['mode'] == 'optimistic_abort' else 0
        for i in xrange(max_op_cnt):
            t_entries.append("table_add t_store_update do_store_update%d %d => %d"%(i,i+1,opti_enabled))
            t_entries.append("table_add t_req_pass1 do_check_op%d %d => %d"%(i,i+1,read_cache_enabled))
            t_entries.append("table_add t_req_fix do_req_fix%d %d =>"%(i,i+1))

    if conf['switch']['mode'] == 'optimistic_abort':
        for i in xrange(max_op_cnt):
            t_entries.append("table_add t_opti_update do_opti_update%d %d =>"%(i,i+1))

    for n, host in enumerate(hosts):
        t_entries.append("table_add send_frame rewrite_mac %d => %s" % (n+1, host['mac']))
        t_entries.append("table_add forward set_dmac %s => %s" % (host['ip'], host['mac']))
        t_entries.append("table_add ipv4_lpm set_nhop %s/32 => %s %d" % (host['ip'], host['ip'], n+1))

    print '\n'.join(t_entries)
    p = subprocess.Popen(['./add_entries_stdin.sh', args.json], stdin=subprocess.PIPE)
    p.communicate(input='\n'.join(t_entries))

    with os.fdopen(os.open(os.path.join(conf['log_dir'], 'summary.txt'), os.O_CREAT | os.O_WRONLY, 0666), 'w') as f:
        cmd_line = ' '.join(['"%s"'%a if ' ' in a else a for a in sys.argv])
        git_rev = subprocess.Popen(["git", "rev-parse", "HEAD"], stdout=subprocess.PIPE).communicate()[0].strip()
        f.write("time: %s\n"%time.strftime("%a, %d %b %Y %H:%M:%S %z"))
        f.write("command: %s\n"%cmd_line)
        f.write("git revision: %s\n"%git_rev)
        f.close()

    devnull = open('/dev/null', 'w')

    server = net.get(hosts[0]['name'])
    server_proc = server.popen(hosts[0]['cmd'], stdout=devnull)
    sleep(0.5)


    return_codes = []
    def _wait_for_client(p, host):
        print p.communicate()
        if p.returncode is None:
            p.wait()
            print p.communicate()
        return_codes.append(p.returncode)
        if 'stdoutfile' in host:
            host['stdoutfile'].flush()
            host['stdoutfile'].close()

    client_procs = []
    for host in hosts[1:]:
        h = net.get(host['name'])
        print h.name, host['cmd']
        pipe_stdout_to = devnull
        if 'stdout_log' in host:
            host['stdoutfile'] = open(host['stdout_log'], 'w')
            pipe_stdout_to = host['stdoutfile']
        p = h.popen(host['cmd'], stdout=pipe_stdout_to)
        if conf['sequential_clients']: _wait_for_client(p, host)
        client_procs.append((p, host))

    if args.cli:
        CLI( net )

    if not conf['sequential_clients']:
        for p, host in client_procs: _wait_for_client(p, host)

    if server_proc.returncode is None:
        server_proc.send_signal(signal.SIGINT)
        sleep(0.2)
        if server_proc.returncode is None:
            server_proc.kill()
        print server_proc.communicate()
        return_codes.append(server_proc.returncode)

    net.stop()

    bad_codes = [rc for rc in return_codes if rc != 0]
    with os.fdopen(os.open(os.path.join(conf['log_dir'], 'done.txt'), os.O_CREAT | os.O_WRONLY, 0666), 'w') as f:
        cmd_line = ' '.join(['"%s"'%a if ' ' in a else a for a in sys.argv])
        f.write("time: %s\n\n"%time.strftime("%a, %d %b %Y %H:%M:%S %z"))
        f.write("Error: %s\n"% ('true' if len(bad_codes) else 'false'))
        f.close()
    if len(bad_codes) > 0: sys.exit(1)

if __name__ == '__main__':
    setLogLevel( 'info' )
    main()
