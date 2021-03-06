#!/usr/bin/env python
import argparse
import socket
import time
import signal
import errno
from Queue import Queue
from threading import Thread, Lock
from gotthard import *
import psutil

class RecvQueue:
    clients = {}

    def __init__(self):
        self.lock = Lock()

    def pushpop(self, req):
        with self.lock:
            return self._pushpop(req)

    # TODO: prune fragmented message queues with missing frags
    def _pushpop(self, req):
        if req.frag_cnt == 1:
            return req.ops
        if req.cl_id not in self.clients:
            self.clients[req.cl_id] = {}
        if req.req_id not in self.clients[req.cl_id]:
            self.clients[req.cl_id][req.req_id] = {}
        self.clients[req.cl_id][req.req_id][req.frag_seq] = req
        if len(self.clients[req.cl_id][req.req_id]) == req.frag_cnt:
            assert set(xrange(1, req.frag_cnt+1)) == set(self.clients[req.cl_id][req.req_id].keys())
            frags = [self.clients[req.cl_id][req.req_id][f] for f in xrange(1, req.frag_cnt+1)]
            ops = [o for req in frags for o in req.ops]
            del self.clients[req.cl_id][req.req_id]
            return ops
        return None

class StoreServer:

    def __init__(self, port=None, mysql=False, verbosity=0, think=0, threads=1, logger=None, recover=None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', port))

        self.verbosity = verbosity
        self.think = think
        self.threads = threads
        self.log = logger
        self.recover_filename = recover

        self.recvq = RecvQueue()
        assert self.threads > 0
        if self.threads > 1:
            self.req_queue = Queue()

        if mysql:
            from mysql_store import MySQLStore
            self.store = MySQLStore()
        else:
            self.store = Store()

        if self.recover_filename: self.recover(self.recover_filename)


    def recover(self, filename):
        with open(filename, 'r') as f:
            self.store.load(f)
        if self.verbosity > 0:
            print "Recovered %d objects from %s" % (len(self.store.values), filename)

    def sendResp(self, req, status, ops, addr):
        frag_cnt = int(math.ceil(len(ops) / float(GOTTHARD_MAX_OP)))

        res = []
        for i in xrange(0, frag_cnt):
            res.append(TxnMsg(replyto=req, status=status,
                frag_seq=i+1, frag_cnt=frag_cnt,
                ops=ops[i*GOTTHARD_MAX_OP:(i*GOTTHARD_MAX_OP)+GOTTHARD_MAX_OP]))

        if len(res) == 0:
            res.append(TxnMsg(replyto=req, status=status))

        for r in res:
            self.sock.sendto(r.pack(), addr)
            if self.verbosity > 1: print "<=", r
            if self.log: self.log.log("sent", res=r)
            wait = min(MIN_INTER_MSG_SEND_WAIT * req.frag_seq, MAX_INTER_MSG_SEND_WAIT)
            if wait:
                time.sleep(wait)

    def handleReq(self, data, addr):
        req = TxnMsg(binstr=data)
        if self.log: self.log.log("received", req=req)
        if self.think: time.sleep(self.think)
        assert req.flags.type == TYPE_REQ

        if self.verbosity > 1: print "=>", req

        if req.flags.reset:
            if self.verbosity > 0: print "Removing all %d objects" % len(self.store.values)
            self.store.clear()
            if self.recover_filename: self.recover(self.recover_filename)
            status, ops = STATUS_OK, []
        else:
            txn_ops = self.recvq.pushpop(req)
            if txn_ops is None:
                return
            if len(txn_ops) == 1 and txn_ops[0].type == TXN_CPU_PCT:
                #print psutil.cpu_percent(percpu=True)
                val = struct.pack('!f', psutil.cpu_percent())
                status, ops = STATUS_OK, [TxnOp(t=TXN_CPU_PCT, key=0, value=val)]
            else:
                status, ops = self.store.applyTxn(txn_ops)

        self.sendResp(req, status, ops, addr)

    def reqThread(self):
        while True:
            e = self.req_queue.get()
            if e is None: break
            data, addr = e
            self.handleReq(data, addr)

    def loop(self):
        req_threads = []
        if self.threads > 1:
            req_threads = [Thread(target=self.reqThread) for _ in xrange(self.threads)]
            for t in req_threads: t.start()

        while True:
            try:
                data, addr = self.sock.recvfrom(MAX_TXNMSG_SIZE)
            except socket.error as (code, msg):
                if code != errno.EINTR:
                    raise
                break
            if self.threads == 1:
                self.handleReq(data, addr)
            else:
                self.req_queue.put((data, addr))

        for _ in req_threads: self.req_queue.put(None)
        for t in req_threads: t.join()



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--port", type=int, help="port to bind on", required=True)
    parser.add_argument("-l", "--log", type=str, help="log file to write to", required=False)
    parser.add_argument("-d", "--dump", type=str, help="dump store to this file on exit", required=False)
    parser.add_argument("-r", "--recover", type=str, help="recover store from this file", required=False)
    parser.add_argument("-v", "--verbosity", type=int, help="set verbosity level", default=0, required=False)
    parser.add_argument("-t", "--think", type=float, help="think time for requests", default=0, required=False)
    parser.add_argument("-j", "--threads", type=int, help="Number of threads", default=1, required=False)
    parser.add_argument("-m", "--mysql", action='store_true', help="Use MySQL as backend DB", default=False, required=False)
    args = parser.parse_args()

    log = None
    if args.log:
        log = GotthardLogger(args.log)

    server = StoreServer(port=args.port, mysql=args.mysql, verbosity=args.verbosity,
            think=args.think, threads=args.threads, logger=log, recover=args.recover)

    def handler(signum, frame):
        if args.dump:
            with open(args.dump, 'w') as f:
                server.store.dump(f)
        if log: log.close()
        server.sock.close()
    signal.signal(signal.SIGINT, handler)

    server.loop()

    if log: log.close()
