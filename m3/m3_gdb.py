#!/usr/bin/env python

#
#
# Andrew Lukefahr
# lukefahr@indiana.edu
#
#

# Coerce Py2k to act more like Py3k
from __future__ import (absolute_import, division, print_function, unicode_literals)
from builtins import (
        ascii, bytes, chr, dict, filter, hex, input, int, isinstance, list, map,
        next, object, oct, open, pow, range, round, str, super, zip,
        )

import binascii
import logging
import os
import queue 
import socket
import struct
import sys
import threading
import time

from m3.m3_mbus import MBusInterface
from m3.m3_mbus import Memory
from m3.m3_mbus import RegFile

#inspired by
# https://github.com/0vercl0k/ollydbg2-python/blob/master/samples/gdbserver/gdbserver.py#L147

try: from . import m3_logging
except ValueError: import logging as m3_logging

class GdbRemote(object):
    '''
    This class handles the interface with the GDB client, and puts
    decoded messages in an output queue accessable through get()

    It also handles formatting responses to the client through the put() command

    It spawns a TX and RX thread internally
    '''

    class UnsupportedException(Exception): pass
    class PortTakenException(Exception): pass
    class DisconnectException(Exception): pass
    class CtrlCException(Exception): pass

    def __init__(this, tcp_port = 10001, log_level = logging.WARN):
        
        # setup our log
        this.log = m3_logging.getLogger( type(this).__name__)
        this.log.setLevel(log_level)
        
        #open our tcp/ip socket
        this.sock = socket.socket( socket.AF_INET, socket.SOCK_STREAM)

        #Bind socket to local host and port
        assert( isinstance(tcp_port,int) )
        try:
            this.sock.bind( ('localhost', tcp_port) )
        except socket.error as msg:
            this.log.error('Bind to port: ' + str(tcp_port) + \
                            ' failed. Error Code : ' + \
                            str(msg[0]) + ' Message ' + msg[1] )
            raise this.PortTakenException()

        this.log.info( 'Bound to port: ' + str(tcp_port))
        
        # inter-thread queues
        this.respQ = queue.Queue()
        this.reqQ = queue.Queue()
    
    def get(this,):
        '''
        Get the next incomming gdb client message 
        Blocking
        '''
        while True:
            try: return this.reqQ.get(True, 10)
            except queue.Empty: pass

    def put(this,msg):
        '''
        Put something on the outgoing (response) stream
        '''
        this.respQ.put(msg)

    def run(this,): 
        '''
        Starts the GDB server frontend
        (non-blocking call)
        '''
        this.RxTid = threading.Thread( target=this._gdb_rx, )
        this.RxTid.daemon = True
        this.RxTid.start()
        this.log.debug("Started GDB Thread")
        
    def _gdb_rx(this):
        ''' 
        Gdb client connection thread
        This thread handles accepting connections and incomming (receive) data
        It spawns a seperate thread for transmitting data
        '''
        this.sock.listen(1) #not sure why 1
        
        while True:
            [conn, client] = this.sock.accept()
            this.log.info('New connection from: ' + str(client[0]))
            
            #grab the opening '+'
            this.log.debug('Grabbing opening +')
            plus = conn.recv(1)
            assert(plus == '+')

            # start a response thread
            TxTid = threading.Thread( target=this._gdb_tx, args=(conn,) )
            TxTid.daemon = True
            TxTid.start()

            while True:
                try:
                    msg  = this._gdb_recv( conn)
                    if msg: 
                        this._process_command(msg)

                except this.CtrlCException:
                    this.log.debug('Caught CTRL+C')
                    this._gdbPut('_ctrlc_')

                except this.DisconnectException: 
                    # this is here to make testing easier, but isn't required
                    this._gdbPut('k')
                    this._gdbPut('_quit_')
                    this.put('GDB_QUIT')
                    break
            
            this.log.info('Closing connection with: ' + str(client[0]))
            conn.close()
            TxTid.join()

    def _gdb_tx(this, conn):
        ''' 
        Gdb Server Transmit thread
        runs until it pulls a 'GDB_QUIT' message from the incomming queue
        '''
        while True:
            msg = this._gdbGet()
            if msg == 'GDB_QUIT': 
                return
            elif msg == '+':
                this.log.debug('TX: ' + str(msg))
                conn.send(msg)
            else:
                this._gdb_resp(conn, msg) 

    def _gdbPut(this, cmd, *args, **kwargs):
        '''
        place a message on the to-be-processed queue
        '''
        this.reqQ.put( (cmd, args, kwargs) )
    
    def _gdbGet(this, timeout=None):
        '''
        get the next response from the queue
        (Blocking)
        '''
        ztime = 10 if timeout == None else timeout
        while True:
            try: return this.respQ.get(True, ztime)
            except queue.Empty: 
                if timeout == None: continue
                else: return None
    
 
    def _process_command(this,cmd):
        '''
        initial processing of a GDB client command
        mostly just puts it on the outgoing queue
        '''
        assert(len(cmd) > 0)

        cmdType = cmd[0]
        subCmd = cmd[1:]
       
        if cmdType == '+': return
        if cmdType == '?': 
            this._gdbPut('_question_')
        elif cmdType in [ 'D', 'c', 'k', 'g', 's' ]: 
            this._gdbPut(cmdType)
        elif cmdType in [ 'M', 'P', 'X', 'Z', 'm', 'p', 'q', 'v', 'z' ]: 
            this._gdbPut(cmdType, subCmd)
        elif cmdType in [ 'H', ]: 
            this._unsupported(cmdType, subCmd)
        else: raise this.UnsupportedException( cmdType)

    def _unsupported(this, cmdType, subCmd):
        '''
        Generic unsupported message response
        '''
        this.log.info("unsupported Type:" + str(cmdType))
        this.log.debug("SubCommand:" + str(subCmd))
        this.put("") #by-pass control thread

    
    def _gdb_recv(this, conn):
        '''
        receives and formats incomming messages
        @conn the connection to the gdb client 
        @throws DisconnectException the connection is disconnected
        @throws CtrlCException the message is 0x03 (CTRL-C)
        '''
    
        while True:
            rawdata= conn.recv(1024)
                    
            if not rawdata:
                raise this.DisconnectException()

            this.log.debug('RX: ' + str(rawdata) ) 

            # CTRL+C
            if chr(0x03) in rawdata:
                raise this.CtrlCException()
            
                # static buffer to tack on the new data
            # (plus fun way to make a static-ish function variable)
            try: this._buf_data += rawdata
            except AttributeError: this._buf_data = rawdata

            # acks "+" at the beginning can be safely removed
            if this._buf_data[0] == '+':
                this._buf_data= this._buf_data[1:]
        
            msg = None
            
            chkIdx = this._buf_data.find('#')
            # look for a checksum marker + 2 checksum bytes
            if (chkIdx > 0) and (len(this._buf_data) >= chkIdx + 3):
                #this.log.debug('Found # at: ' + str(chkIdx) )

                # get the message and checksum
                assert(this._buf_data[0] == '$')
                msg = this._buf_data[1:chkIdx]
                msgSum = int(this._buf_data[chkIdx+1:chkIdx+3],16)

                calcSum= 0
                for byte in msg:
                    calcSum = (calcSum + ord(byte)) & 0xff

                if calcSum != msgSum:
                    raise Exception("Checksum Error")
                else:
                    #this.log.debug('Checksum pass')
                    pass
                
                if '}' in msg:
                    raise Exception("FIXME: escape sequence")

                this._buf_data = this._buf_data[chkIdx+3:]

                this.log.debug('Parsed message : ' + str(msg) ) 
                #this.log.debug('Advanced buffered data: ' + \
                    # str(this._buf_data) ) 

                #ack message
                this.put('+') # bypass CTRL 

            return msg

    def _gdb_resp(this, conn, msg):
        '''
        takes a raw msg and formats it, and sends it over the connection
        to the GDB client 

        should not be used for '+' messages
        '''
        # calc checksum
        chkSum = 0
        for c in msg:
            chkSum += ord(c)
        chkSum = chkSum & 0xff
        
        if '}' in msg:
            raise Exception("FIXME: escape sequence")

        gdb_msg = '$%s#%.2x' % (msg, chkSum)
       
        this.log.debug('TX: ' + str(gdb_msg))
        conn.send( gdb_msg )



class GdbCtrl(object):
    '''
    The backend controller that actually impliments the GDB commands
    on the PRC over MBus
    '''

    class PrcCtrl(object):
        '''
        Manages the PRC, allows for out-of-band halt responses
        '''
        
        class HaltMBusInterface(MBusInterface):
            ''' 
            Overloads the MBus interface, allowing a special _callback
            '''
            def __init__(this, halt, _ice, prc_addr, log_level):
                super( halt.HaltMBusInterface, this).__init__(_ice, prc_addr,
                                                        log_level)
                this.halt = halt

            def _callback(this,*args, **kwargs):
                this.callback_queue.put((time.time(), args, kwargs))
                # only put special messages in the halt.queue...
                mbus_addr, mbus_data = args     
                [mbus_addr_int] = struct.unpack(">I", mbus_addr)
                if mbus_addr_int == 0xe0:
                    this.halt.queue.put( (mbus_addr, mbus_data) )
         
        class HaltRegFile(RegFile):
            ''' 
            Overloads the RegFile interface, 
            allowing thread-safeness for base_addr updates
            '''
            def __init__(this, halt, mbus, base_addr, writeback, log_level):
                super(halt.HaltRegFile, this).__init__(mbus, base_addr, \
                                        writeback, log_level)
                this.halt = halt
            def update_base_addr(this, base_addr):
                with this.halt.lock:
                    return RegFile.update_base_addr(this,base_addr)
            def __getitem__(this,key):
                assert(this.halt.flag_addr != None)
                with this.halt.lock:
                    return RegFile.__getitem__(this,key)
            def __setitem__(this,key,val):
                assert(this.halt.flag_addr != None)
                with this.halt.lock:
                    return RegFile.__setitem__(this,key,val)
            def forceWrite(this,key,val):
                assert(this.halt.flag_addr != None)
                with this.halt.lock:
                    return RegFile.forceWrite(this,key,val)

        def __init__(this, _ice, prc_addr, \
                                        log_level = logging.WARN):
            ''' 
            Manages the PRC through MBus.  

            Provides a memory and register file interface

            Also starts a halt-monitoring thread for out-of-band halt responses
            '''
            this.log = m3_logging.getLogger( type(this).__name__)
            this.log.setLevel(log_level)
            
            # the addr of the interrupt flag, None = not interrupted
            this.flag_addr = None

            # what we call when halt is triggered
            this.halt_cb = None

            this.lock = threading.RLock()
            this.queue = queue.Queue() # incomming PRC halt messages
            this.stop = threading.Event() # set this to stop the halt thread

            this.tid= threading.Thread( target=this._halt_thread, )
            this.tid.daemon = True
            this.tid.start()
            this.log.debug("Started Halt Thread")
            
            #our standard MBUS interfaces
            this.mbus = this.HaltMBusInterface( this, _ice, prc_addr, \
                                    log_level=log_level)
            this.mem = Memory(this.mbus, writeback=False, \
                                    log_level=log_level)
            this.rf = this.HaltRegFile(this,this.mbus,None,writeback=False, \
                                    log_level=log_level)
            this.log.debug("Created MBus/RF/Mem interfaces")
        
        def getMem(this): 
            ''' returns the PRC's memory abstraction'''
            return this.mem
        def getRF(this): 
            ''' returns the (thread-safe) PRC's register abstraction'''
            return this.rf
       
        def halt(this, halt_cb):
            '''
            non-blocking request to halt
            will call halt_cb("S05") when halt is complete
             possibly overwriting any existing halt_cb
            '''
            with this.lock:
                this.log.debug("Halting the PRC")
                assert(this.flag_addr == None)
                if (this.halt_cb):
                    this.log.debug("Overwriting existing halt_cb")
                this.halt_cb = halt_cb
                this.mbus.write_reg( 0x7, 0x1) # write something to MBUS_R7
                this.flag_addr = None
        
        def resume(this, halt_cb=None):
            '''
            resume the halted PRC
            will call halt_cb("S05") when halted if argument is given
            '''
            with this.lock:
                assert(this.flag_addr != None)
                assert(this.halt_cb == None)
                this.halt_cb = halt_cb
                this.log.debug("Resuming the PRC")
                this.log.debug("clearing flag @" + hex(this.flag_addr))
                this.mbus.write_mem(this.flag_addr, 0x01, 32)
                this.flag_addr = None
        
        def isHalted(this):
            ''' returns true is the PRC is currently soft-halted '''
            return (this.flag_addr != None)

        def _halt_thread(this):
            ''' 
            halt management thread, waits for halt to be triggered, 
            then calls halt_cb("S05") if halt_cb is valid 
            '''
            while not this.stop.isSet():
                try:  
                    mbus_addr, mbus_data = this.queue.get( True, 10)
                    this.log.debug("HALT triggered")

                    # read the gdb_flag and register pointer
                    [mbus_addr] = struct.unpack(">I", mbus_addr)
                    assert( mbus_addr == 0xe0)
                    [flag_addr ] = struct.unpack(">I", mbus_data)
                    this.log.debug("flag triggered")
                    this.log.debug("flag at: " + hex(flag_addr))
                except queue.Empty: continue

                try:
                    mbus_addr, mbus_data = this.queue.get()
                    [mbus_addr] = struct.unpack(">I", mbus_addr)
                    assert( mbus_addr == 0xe0)
                    [reg_addr ] = struct.unpack(">I", mbus_data)
                    this.log.debug("updating regFile at: " + hex(reg_addr))
                except Exception as e:
                    print (e)
                    raise
                
                with this.lock: 
                    this.rf.update_base_addr(reg_addr)
                    this.flag_addr = flag_addr
                    this.log.debug("Responding via GDB with SIGTRAP")
                    try:
                        this.halt_cb("S05")
                    except TypeError:
                        this.log.debug("skipping halt callback, not registered?")
                    this.halt_cb = None


    def __init__(this, ice, frontend, prc_addr, log_level = logging.WARN):

        # setup our log
        this.log = m3_logging.getLogger( type(this).__name__)
        this.log.setLevel(log_level)
       
        this.fe = frontend # the gdb frontend
        
        # the processor interface, and it's memory + reg file
        this.prc= this.PrcCtrl( ice, prc_addr, log_level)
        this.mem = this.prc.getMem()
        this.rf = this.prc.getRF()

        this.svc_01 = 0xdf01 # asm("SVC #01")

        # were displaced instructions live
        # these have the form { (addr,size) : inst }
        this.displaced_insts = {} 

        # try to import PyMulator (used to fake single-stepping)
        try:
            from PyMulator.PyMulator import PyMulator
            this.mulator = PyMulator(this.rf, this.mem,debug=True)
        except ImportError: 
            this.mulator = None
            this.log.warn('='*40 + '\n' + \
                         '\tPyMulator not found\n' +\
                         '\tSingle-stepping will not work!\n' + \
                         '\t Please install PyMulator:\n' +\
                         '\t $ pip install PyMulator\n' +\
                         '='*40)
                                         

        this.regs = [   'r0', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6', 
                        'r7', 'r8', 'r9', 'r10', 'r11', 'r12', 'sp', 
                        'lr', 'pc', 
                        'f0', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7',                                 'fps', 
                        'xpsr', ]
        # how much 0-padding to put in front of a register 
        this.regsPads= { 'r0':0, 'r1':0, 'r2':0, 'r3':0, 'r4':0, 
                        'r5':0, 'r6':0, 'r7':0, 'r8':0, 'r9':0, 
                        'r10':0, 'r11':0, 'r12':0, 'sp':0, 'lr':0, 
                        'pc':0, 
                        'f0':8, 'f1':8, 'f2':8, 'f3':8, 'f4':8, 
                        'f5':8, 'f6':8, 'f7':8, 'fps':0, 
                        'xpsr':0, }

        this.encode_str = { 4:'<I', 2:'<H', 1:'<B' }
        
    def cmd__question_(this,):
        this.log.info("? Cmd")
        if this.prc.isHalted():
            return 'S05'
        else:
            this.cmd__ctrlc_()

    def cmd__ctrlc_(this,):
        this.log.info("CTRL+C (HALT)")
        this.prc.halt( this.fe.put )

    def cmd_D(this):
        this.log.info("detach")

        #halt the PRC if not already
        if not this.prc.isHalted(): 
            this.prc.halt(None)
        while not this.prc.isHalted():
            time.sleep(0.5)
        
        # remove all breakpoints, clear the table
        this.log.debug('removing breakpoints')
        for (addr,size) in this.displaced_insts:
            orig_inst = this.displaced_insts[(addr,size)]
            this.mem.forceWrite( (addr,size), orig_inst)
        this.displaced_insts = {}
        
        #then resume the PRC
        this.log.debug('resuming PRC')
        this.prc.resume()
        
        return 'OK'

    def cmd_M(this, subcmd):
        preamble, data = subcmd.split(':')
        addr,size_bytes= preamble.split(',')
        addr,size_bytes = map(lambda x: int(x, 16), [addr, size_bytes])
        this.log.info('mem write: ' + hex(addr) + ' of ' + str(size_bytes))
        data = binascii.unhexlify(data) 
        
        while size_bytes > 0:
            b = struct.unpack("B", data[0])[0]
            this.log.debug('Writing ' + hex(b) + ' to ' \
                + hex(addr))

            #this is not the most efficient, but it works...
            this.mem.forceWrite((addr,8), b)

            size_bytes -= 1
            addr += 1
            data = data[1:]
        return 'OK'

    def cmd_P(this, subcmd):
        assert(this.prc.isHalted()) 
        reg,val = subcmd.split('=')
        reg = int(reg, 16)
        reg = this.regs[ reg]
        # fix endiananess, conver to int
        val = int(binascii.hexlify( binascii.unhexlify(val)[::-1]),16)
        this.log.info('register write :' + str(reg) + ' = ' + hex(val) )
        this.rf.forceWrite(reg,val)
        return "OK"

    def cmd_X(this, subcmd):
        this.log.info("Binary Memory Write not supported.")
        return ""

    def cmd_Z(this, subcmd):
        args = subcmd.split(',')
        brType, addr, size = map(lambda x: int(x,16), args)
        assert(brType == 0)
        assert(size == 2)
        this.log.info("Breakpoint Set, Type:" + str(brType) + \
                                " Addr: " + hex(addr))
        this.log.debug("Replacing instruction @" + \
                        hex(addr) + " with trap" )
        size *= 8 # convert to bits
        if (addr,size) in this.displaced_insts: 
            this.log.debug( hex(addr) + '('+str(size)+')' + \
                'already a soft-breakpoint')
        else:
            this.displaced_insts[(addr,size)] = \
                    this.mem[(addr,size)]
            this.mem.forceWrite( (addr,16), this.svc_01)
        return 'OK'


    def cmd_c(this):
        assert(this.prc.isHalted()) 
        this.log.info("continue")
        this.prc.resume( this.fe.put )

    def cmd_g(this, ):
        assert(this.prc.isHalted()) 
        this.log.info('read all regs')

        resp = ''
        for ix in range(0, len(this.regs)):
            val = this.cmd_p( this.regs[ix] )
            resp += val
        return resp

    def cmd_k(this):
        this.log.info("kill")
        # just resume the PRC?  
        if this.prc.isHalted():
            this.prc.resume()

    def cmd_m(this, subcmd):
        assert(this.prc.isHalted()) 
        args = subcmd.split(',')
        addr,size_bytes = map(lambda x: int(x, 16), args)

        this.log.info('mem read: ' + hex(addr) + ' of ' + \
                            str(size_bytes))

        resp = '' 
        while size_bytes > 0:
            read_bytes = 4 if size_bytes >4 else size_bytes
            encode_str = this.encode_str[read_bytes]
            val = this.mem[(addr,read_bytes * 8)]
            this.log.debug('mem read: ' + hex(addr) + ' ' + hex(val))
            val = struct.pack(encode_str, val).encode('hex')#lit endian
            resp += val
            addr += read_bytes
            size_bytes -= read_bytes
        return resp

                
    def cmd_p(this, subcmd):
        assert(this.prc.isHalted()) 
        reg = subcmd
        this.log.info('reg read: ' + str(reg))
        encode = this.encode_str[4]

        val = this.rf[reg]
        if reg == 'pc': val -= 4
        val = struct.pack(encode ,val).encode('hex') #lit endian 
        val = '00' * this.regsPads[reg] + val # add some front-padding
        return val

    def cmd_q(this, subcmd):
        this.log.info('Query')
        if subcmd.startswith('C'):
            # asking about current thread, 
            # again, what threads...
            return ""
        elif subcmd.startswith('fThreadInfo'):
            # info on threads? what threads?
            return ""
        elif subcmd.startswith('L'):
            # legacy form of fThreadInfo
            return ""
        elif subcmd.startswith('Attached'):
            # did we attach to a process, or spawn a new one
            # processes?
            return ""
        elif subcmd.startswith('Offsets'):
            # did we translate the sections vith virtual memory?
            # virtual memory?
            return ""
        elif subcmd.startswith('Supported'):
            # startup command
            this.log.debug('qSupported')
            return "PacketSize=4096"
        elif subcmd.startswith('Symbol'):
            # gdb is offering us the symbol table
            return "OK"
        elif subcmd.startswith('TStatus'):
            #this has to do with tracing, we're not handling that yet
            return ""
        else: raise this.UnsupportedException( subcmd)
       
    def cmd_s(this, ):
        assert(this.prc.isHalted()) 
        this.log.info('single-step ')

        # might have to temporarially replace a trap
        displaced_trap = False
        pc = this.rf['pc'] - 4
        
        if (pc,16) in this.displaced_insts:
            this.log.debug("Trap @ " + hex(pc) + \
                    ", but we need the inst, fixing...")
            this.cmd_z('0,' + hex(pc)[2:] + ',2')
            displaced_trap = True

        # if the prc is halted, the reg file is valid 
        if True:
            this.log.debug("Soft-Stepping with Mulator")
            try:
                this.mulator.stepi()
            except AttributeError: 
                this.log.error("Stepping without PyMulator")
                this.log.error("CRASHING")
                raise

            break_addr = this.rf.getLocal('pc') - 4
            this.log.debug("Next PC: " + hex(break_addr) )
       
        # insert soft-trap at next instruction
        this.cmd_Z('0,' + hex(break_addr)[2:] + ',2')
        
        #step to the soft-trap
        this.prc.resume()
        while not this.prc.isHalted():
            time.sleep(0.1)
        
        #fix the next instruction
        this.cmd_z('0,' + hex(break_addr)[2:] + ',2')
        #and the orig inst
        if displaced_trap:
            this.cmd_Z('0,' + hex(pc)[2:] + ',2')

        return 'S05'

    def cmd_v(this, subcmd):
        this.log.info('v command: ' + str(subcmd))
        if subcmd.startswith('Cont?'):
            assert(this.prc.isHalted() )
            return "vCont;cs"
        elif subcmd.startswith('MustReplyEmpty'):
            return ""
        else: 
            this.log.warn("Unrecognized v command")
            return ""

    def cmd_z(this, subcmd):
        args = subcmd.split(',')
        brType, addr, size = map(lambda x: int(x,16), args)
        assert(brType == 0)
        assert(size == 2)
        this.log.info("Breakpoint Clear, Type: " + str(brType) + \
                        " Addr:" + hex(addr))
        this.log.debug("Replacing trap with origional instruction @" +\
                        hex(addr) )
        size *= 8 # convert to bites                                
        if (addr,size) not in this.displaced_insts:
            this.log.info( hex(addr) + '('+str(size)+')' + \
                'not a soft-breakpoint')
        else:
            orig_inst = this.displaced_insts[(addr,size)]
            this.mem.forceWrite( (addr,size), orig_inst)
            del this.displaced_insts[(addr,size)]
        return 'OK'



#
#
#
class test_GdbCtrl(GdbCtrl):
    ''' 
    A testing interface for the GDB Server frontend
    Just reports dummy values
    '''
    
    def __init__(this):

        this.log = m3_logging.getLogger( type(this).__name__)
        this.log.setLevel(logging.DEBUG)
        this.regs = [   'r0', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8', 
                        'r9', 'r10', 'r11', 'r12', 'sp', 'lr', 'pc', 
                        'f0', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'fps', 
                        'xpsr', ]
        this.regsPads= { 'r0':0, 'r1':0, 'r2':0, 'r3':0, 'r4':0, 'r5':0, 
                        'r6':0, 'r7':0, 'r8':0, 'r9':0, 'r10':0, 'r11':0, 
                        'r12':0, 'sp':0, 'lr':0, 'pc':0, 
                        'f0':8, 'f1':8, 'f2':8, 'f3':8, 'f4':8, 'f5':8, 'f6':8, 
                        'f7':8, 'fps':0, 
                        'xpsr':0, }

    def cmd__question_(this,):
        this.log.info("? Cmd")
        return 'S05'

    def cmd__ctrlc_(this,):
        this.log.info("CTRL+C (HALT)")
        #hack for now...
        return this.cmd__question_()

    def cmd_D(this):
        this.log.info("detach")
        return 'OK'

    def cmd_M(this, subcmd):
        preamble, data = subcmd.split(':')
        addr,size_bytes= preamble.split(',')
        addr,size_bytes = map(lambda x: int(x, 16), [addr, size_bytes])
        this.log.info('mem write: ' + hex(addr) + ' of ' + str(size_bytes))
        data = binascii.unhexlify(data) 
        while size_bytes > 0:
            b = struct.unpack("B", data[0])[0]
            this.log.debug('Writing ' + hex(b) + ' to ' \
                + hex(addr))

            size_bytes -= wr_size
            addr += wr_size 
            data = data[wr_size:]
        return 'OK'

    def cmd_P(this, subcmd):
        reg,val = subcmd.split('=')
        reg = int(reg, 16)
        reg = this.regs[ reg]
        # fix endiananess, conver to int
        val = int(binascii.hexlify( binascii.unhexlify(val)[::-1]),16)
        this.log.warn('register write :' + str(reg) + ' = ' + hex(val) )
        return "OK"

    def cmd_X(this, subcmd):
        this.log.info("Binary Memory Write not supported.")
        return ""

    def cmd_Z(this, subcmd):
        zType,addr,size= subcmd.split(',')
        this.log.info('breakpoint set: ' + addr + 'type: ' + zType )
        return 'OK'

    def cmd_c(this):
        this.log.info("continue")

    def cmd_g(this, ):
        this.log.debug('Read all Regs')
        resp = ''
        for ix in range(0, len(this.regs)):
            val = this.cmd_p( this.regs[ix] )
            resp += val
        return resp

    def cmd_k(this):
        this.log.info("kill")

    def cmd_m(this, subcmd):
        addr,size_bytes= subcmd.split(',')
        addr,size_bytes = map(lambda x: int(x, 16), [addr, size_bytes])
        this.log.info('mem read: ' + hex(addr) + ' of ' + str(size_bytes))
        if size_bytes == 4:
            return struct.pack('<I',0x46c046c0).encode('hex') #lit endian hex
        else: 
            return struct.pack('<H',0x46c).encode('hex') #lit endian hex

    def cmd_p(this, subcmd):
        reg = subcmd
        this.log.info('reg_read: ' + str(reg))
        val = 0x1234
        val = struct.pack('<I',val).encode('hex') #lit endian hex
        val = '00' * this.regsPads[reg] + val # add some front-padding
        return val

    def cmd_q(this, subcmd):
        this.log.debug('Query')
        if subcmd.startswith('C'):
            # asking about current thread, 
            # again, what threads...
            return ""
        elif subcmd.startswith('fThreadInfo'):
            # info on threads? what threads?
            return ""
        elif subcmd.startswith('L'):
            # legacy form of fThreadInfo
            return ""
        elif subcmd.startswith('Attached'):
            # did we attach to a process, or spawn a new one
            # processes?
            return ""
        elif subcmd.startswith('Offsets'):
            # did we translate the sections vith virtual memory?
            # virtual memory?
            return ""
        elif subcmd.startswith('Supported'):
            # startup command
            this.log.debug('qSupported')
            return "PacketSize=4096"
        elif subcmd.startswith('Symbol'):
            # gdb is offering us the symbol table
            return "OK"
        elif subcmd.startswith('TStatus'):
            #this has to do with tracing, we're not handling that yet
            return ""
        else: raise this.UnsupportedException( subcmd)


    def cmd_s(this, ):
        this.log.info('single-step ')
        return 'S05'
    
    def cmd_v(this, subcmd):
        if subcmd.startswith('Cont?'):
            this.log.debug('vCont')
            return "vCont;cs"
        else: assert(False) 

    def cmd_z(this, subcmd):
        ztype,addr,size= subcmd.split(',')
        this.log.info('breakpoint clear: ' + (addr))
        return 'OK'


if __name__ == '__main__':
   
    logging.basicConfig( level=logging.WARN, 
                            format='%(levelname)s %(name)s %(message)s')

    port = 10001

    if (len(sys.argv) > 1):
        for arg in sys.argv:
            if '--port=' in arg:
               port = int(arg.split("=")[1])
               print ('set port=' + str(port))

    ctrl = test_GdbCtrl()
    gdb = GdbRemote( tcp_port=port, log_level = logging.DEBUG)
    gdb.run()

    while True: 
        cmd, args, kwargs = gdb.get()
        cmd = 'cmd_'+cmd

        if cmd == 'cmd__quit_': 
            print ('GDB CTRL Quiting')
            break
        else : 
            func = getattr(ctrl, cmd)
            ret = func(*args, **kwargs)
            if ret != None: gdb.put(ret)

