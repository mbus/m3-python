#!/usr/bin/env python

import datetime
import os
import sys
import struct
import socket
import tempfile
import time
import binascii

import logging
logger = logging.getLogger('program')

import threading
import subprocess

import inspect

from m3.m3_gdb import GdbRemote
from m3.m3_gdb import test_GdbCtrl

class TestGdbSimple(object):

    _multiprocess_shared_ = False

    class TestFailedException(Exception):
        logger.info('='*42 + '\nTEST FAILED\n' + '='*42)
        pass

    @classmethod
    def setup_class(cls, ):
       
        cls.log = logging.getLogger(cls.__name__)

        cls.port = 10001
        
        cls.gdb = None
        while cls.gdb == None: 
            try:
                cls.gdb = GdbRemote( cls.port, log_level = logging.DEBUG)
            except GdbRemote.PortTakenException:
                cls.log.info('Using alternate port ' + str(cls.port) )
                cls.port += 1

        cls.gdb.run()

       
    @classmethod
    def teardown_class(cls):
        pass


    def test_interface(this):
        
        this.log.info("Testing GDB Interface")
       
        BUF_SIZE = 2048

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect( ('localhost',this.port))
        s.send('+')

        this.log.info("qSupported")
        tx_cmd ='$qSupported:multiprocess+;swbreak+;hwbreak+;qRelocInsn+#c9' 
        s.send(tx_cmd)
        rx_cmd, rx_subcmd, _ = this.gdb.get()
        print rx_cmd
        print rx_subcmd
        rx_cmd += rx_subcmd[0]
        assert( rx_cmd ==  tx_cmd[1:-3])
        
        plus = s.recv(1)
        assert( plus == '+')

        tx_cmd = '$PacketSize=4096#03'
        this.gdb.put(tx_cmd[1:-3])
        rx_cmd = s.recv(BUF_SIZE)
        assert( rx_cmd == tx_cmd)

        s.close()
        
        rx_cmd, rx_subcmd, _ = this.gdb.get()
        assert(rx_cmd == '_quit_')

    def test_gdb_simple(this):
        
        this.log.info("Testing GDB Dummy Backend")

        def ctrl(gdb):
            ctrl = test_GdbCtrl()
            while True:
                cmd,args,kwargs = gdb.get()
                cmd = 'cmd_' + cmd
                if cmd == 'cmd__quit_':
                    this.log.info('GDB CTRL Quitting')
                    return
                func = getattr(ctrl, cmd)
                ret = func(*args, **kwargs)
                if ret != None: gdb.put(ret)
        
        def cmd(sock, cmd):
           this.log.debug('TX: ' + cmd)
           s.send(cmd)
           plus = s.recv(1)
           this.log.debug('plus: ' + cmd)
           assert(plus == '+')
           rx_resp = s.recv(4096)
           this.log.debug('resp: ' + cmd)
           s.send('+')
           return rx_resp

        # this time we launch a thread to run the ctrl interface
        gdb_ctrl = threading.Thread(target = ctrl, args=(this.gdb,))
        gdb_ctrl.daemon = True
        gdb_ctrl.start()

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect( ('localhost',this.port))
        s.send('+')

        this.log.info("qSupported")
        tx_cmd ='$qSupported:multiprocess+;swbreak+;hwbreak+;qRelocInsn+#c9' 
        rx_resp = cmd( s, tx_cmd)
        assert( rx_resp == '$PacketSize=4096#03' )
        
        rx_resp = cmd( s, '$Hg0#df')
        assert(rx_resp == '$#00')

        rx_resp = cmd( s,'$qTStatus#49')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$?#3f')
        assert( rx_resp == '$S05#b8')

        rx_resp = cmd( s,'$qfThreadInfo#bb')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$qL1200000000000000000#50')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$Hc-1#09')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$qC#b4')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$qAttached#8f')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$qOffsets#4b')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$Hg0#df')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$g#67')
        assert( rx_resp == '$341200003412000034120000341200003412000034120000341200003412'\
             '000034120000341200003412000034120000341200003412000034120000341200000000000000000'\
             '000341200000000000000000000341200000000000000000000341200000000000000000000341200'\
             '000000000000000000341200000000000000000000341200000000000000000000341200000000000'\
             '000000000341200003412000034120000#04')

        rx_resp = cmd( s,'$m1234,4#97')
        assert( rx_resp == '$c046c046#fa')

        rx_resp = cmd( s,'$m1234,4#97')
        assert( rx_resp == '$c046c046#fa')

        rx_resp = cmd( s,'$m3a0,2#8f')
        assert( rx_resp == '$6c04#fd')

        rx_resp = cmd( s,'$m3a0,2#8f')
        assert( rx_resp == '$6c04#fd')

        rx_resp = cmd( s,'$m3a0,2#8f')
        assert( rx_resp == '$6c04#fd')

        rx_resp = cmd( s,'$qL1200000000000000000#50')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$qSymbol::#5b')
        assert( rx_resp == '$OK#9a')

        rx_resp = cmd( s,'$m3a0,2#8f')
        assert( rx_resp == '$6c04#fd')

        rx_resp = cmd( s,'$Z0,3a0,2#d8')
        assert( rx_resp == '$OK#9a')

        rx_resp = cmd( s,'$vCont?#49')
        assert( rx_resp == '$vCont;cs#1b')

        rx_resp = cmd( s,'$Hc0#db')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$s#73')
        assert( rx_resp == '$S05#b8')

        rx_resp = cmd( s,'$g#67')
        assert( rx_resp == '$341200003412000034120000341200003412000034120000341200003412'\
            '000034120000341200003412000034120000341200003412000034120000341200000000000000000'\
            '000341200000000000000000000341200000000000000000000341200000000000000000000341200'\
            '000000000000000000341200000000000000000000341200000000000000000000341200000000000'\
            '000000000341200003412000034120000#04')

        rx_resp = cmd( s,'$m1234,4#97')
        assert( rx_resp == '$c046c046#fa')

        rx_resp = cmd( s,'$m1234,4#97')
        assert( rx_resp == '$c046c046#fa')

        rx_resp = cmd( s,'$qL1200000000000000000#50')
        assert( rx_resp == '$#00')

        rx_resp = cmd( s,'$z0,3a0,2#f8')
        assert( rx_resp == '$OK#9a')
       
        rx_resp = cmd( s, '$P8=2a000000#78')
        assert( rx_resp == '$OK#9a')

        s.close()

        gdb_ctrl.join()
        

# Now I have an idea how this works.....
if __name__ == '__main__':

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s - %(message)s")

    import nose
    result = nose.run( defaultTest=__name__, )

    if result == True:
        print 'TESTS PASSED'
    else:
        print 'TESTS FAILED'

