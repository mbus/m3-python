#!/usr/bin/env python

#
# Code to allow the ICE board to interact with the PRC
# via MBUS
#
#
# Andrew Lukefahr
# lukefahr@indiana.edu
#
#

#from pdb import set_trace as bp



# Coerce Py2k to act more like Py3k
from __future__ import (absolute_import, division, print_function, unicode_literals)
from builtins import (
        ascii, bytes, chr, dict, filter, hex, input, int, isinstance, list, map,
        next, object, oct, open, pow, range, round, str, super, zip,
        )

import argparse
import atexit
import binascii
import csv
import inspect
import logging
import os
import sys
import socket
import queue as Queue
import time
import threading
import multiprocessing

import struct

# if Py2K:
import imp

from . import __version__ 


from . import m3_logging
logger = m3_logging.getLogger(__name__)
 
class MBusInterface(object):
    
    class UnalignedException(Exception): pass
    class MBusInterfaceException(Exception): pass

    '''
    A class to wrap MBus into simple read/write commands
    '''

    #
    def __init__(this, _ice, prc_addr, log_level = logging.WARN):
        this.ice = _ice
        this.ice_addr = 0xe

        if (prc_addr > 0x0 and prc_addr < 0xf):
            this.prc_addr = prc_addr
        elif (prc_addr >= 0xf0000 and prc_addr < 0xfffff):
            raise MBusInterfaceException("Only short prefixes supported")
        else: raise MBusInterfaceException("Bad MBUS Addr")

        this.log = m3_logging.getLogger( type(this).__name__)
        this.log.setLevel(log_level)

        this.log.info("MBUS Re-configuring ICE MBus to listen for "+\
                    "Debug Packets")
        this.ice.mbus_set_internal_reset(True)
        this.ice.mbus_set_snoop(False)
        this.ice.mbus_set_short_prefix( hex(this.ice_addr))
        this.ice.mbus_set_internal_reset(False)

        #register the callback
        this.callback_queue = Queue.Queue()
        this.ice.msg_handler['b++'] = this._callback
        #this.ice.msg_handler['B++'] = this._callback

    #
    def _callback(this,*args, **kwargs):
        this.callback_queue.put((time.time(), args, kwargs))
   
    #
    def read(this):
        while True:
            try:
                _, [mbus_addr, mbus_data], _ = this.callback_queue.get(True,10)
                return [mbus_addr, mbus_data]
            except Queue.Empty:  continue

    #
    def read_mem(this,addr,size):
        this.log.debug("MBUS Requesting " + hex(addr))
        
        #first, find the 32-bit word around addr
        align32 = this._align32(addr,size)

        #third, form the request message
        this.log.debug("MBUS Requesting the full word @ " + hex(align32))
        prc_memrd = struct.pack(">I", ( this.prc_addr << 4) | 0x3 ) 
        memrd_reply = struct.pack(">I",  0xe1000000)
        memrd_addr = struct.pack(">I", align32) 
        memrd_resp_addr = struct.pack(">I", 0x00000000)
        this.ice.mbus_send(prc_memrd, 
                    memrd_reply + memrd_addr +  memrd_resp_addr )
        this.log.debug("MBUS Request sent")

        #fourth, wait for a response
        while True: 
            [mbus_addr, mbus_data]= this.read()
            [mbus_addr] = struct.unpack(">I", mbus_addr)
            if (mbus_addr == 0xe1):
                [mem_addr, mem_data] = struct.unpack(">II", mbus_data)
                assert( mem_addr == 0x00000000)
                break
            else: 
                this.log.debug('Found non-e1 MBUS message:' + \
                        hex(mbus_addr) + ' ' + str(repr(mbus_data)))
                continue # try again

        this.log.debug( "MBUS Received: " + hex(align32) + " = " \
                        + hex(mem_data) )
        
        #fifth, split data back to requested size
        mask = 2 ** size - 1
        if size == 32: shift = 0
        elif size == 16: shift = 8*(addr & 0x02) 
        elif size == 8:  shift = 8*(addr & 0x03)

        mem_data = mem_data >> shift
        mem_data = mem_data & mask

        return mem_data

    #
    def write_mem(this, addr, value, size):
        
        this.log.debug('MBUS Writing ' + hex(value) + ' to ' + hex(addr))
        
        assert(isinstance(addr, int))
        assert(isinstance(value, int))
        assert(size in [32,16,8])

        align32 = this._align32(addr,size)

        if size == 32:
            write32 = value

        elif size == 16:
            byte_idx = addr & 0x02
            mask32 = ((2 ** size -1) << (8 * byte_idx))
            mask32n = 0xffffffff - mask32 # bitwise not hack

            orig32 = this.read_mem(align32,32)
            value32 =  value << size | value # just duplicate it
            value32 = value32 & mask32 # and mask it

            write32 = orig32 & mask32n 
            write32 = write32 | value32

        elif size == 8:
            byte_idx = addr & 0x3
            mask32 = ((2 ** size -1) << (8 * byte_idx))
            mask32n = 0xffffffff - mask32 # bitwise not hack
            
            orig32 = this.read_mem(align32,32)
            value32 = (value << (8 * byte_idx)) 
            value32 = value32 & mask32

            write32 = orig32 & mask32n
            write32 = write32 | value32

        this.log.debug("MBUS Writing " + hex(write32) + " @ " + \
                hex(align32))
        prc_memwr = struct.pack(">I", ( this.prc_addr << 4) | 0x2 ) 
        memwr_addr = struct.pack(">I", align32)  
        memwr_data = struct.pack(">I", write32)
        this.ice.mbus_send(prc_memwr, 
            memwr_addr + memwr_data) 
    
    #
    def write_reg(this, reg, val):

        this.log.debug('MBUS: writing register: ' + str(reg) + '=' + hex(val) )
        assert( reg < 8)
        assert( val < ((2 ** 24) -1) )

        mbus_regwr = struct.pack(">I", ( this.prc_addr << 4) | 0x0 ) 
        data = struct.pack(">I", reg << 24 | val )
        this.ice.mbus_send(mbus_regwr, data)


    #
    def _align32(this,addr,size):

        align32 =  addr & 0xfffffffc

        if size == 32:
            if not ( align32 == ((addr + 3) & 0xfffffffc)):
                raise this.UnalignedException()
        elif size == 16:
            if not ( align32 == ((addr + 1) & 0xfffffffc)):
                raise this.UnalignedException()
        
        return align32


#
class Memory(object):
    '''
    Allows dictionary-like access to the M3's memory
    '''
    
    #
    def __init__(this, mbus, writeback=False, log_level = logging.WARN):
        assert( isinstance(mbus, MBusInterface))
        this.mbus = mbus
        this.writeback = writeback
        this.local = {}

        this.log = m3_logging.getLogger( type(this).__name__)
        this.log.setLevel(log_level)

    #
    def __getitem__(this,key):
        addr = key[0]
        size = key[1]
        this.log.debug("MemRd: (" + hex(addr) + ',' + str(size) + ')')
        assert( isinstance(addr, int))
        try:
            return this.mbus.read_mem(addr,size)
        except this.mbus.UnalignedException: 
            # looks like we do it the hard way
            this.log.debug('MemRd: unaligned access')
            assert( size in [32,16] )
            val = 0
            offset = 0
            while size > 0:
                tval = this.mbus.read_mem(addr,8)
                this.log.debug('MemRd: partial read: ' + hex(tval) )
                assert(tval <= 0xff)
                val = val | (tval << offset)
                size -= 8
                offset += 8
                addr += 1
            return val

    #
    def __setitem__(this,key,val):
        '''
        note: by default this only caches updates locally
        '''
        this.log.debug("MemWr: " + str(key) + ':' + str(val))
        addr = key[0]
        size = key[1]
        assert( isinstance(addr, int))
        assert( isinstance(val, int))
        if this.writeback:
            this.mbus.write_mem(addr,val,size)
        else:
            this.local[key] = val # not the best, but ehh

    #
    def forceWrite(this,key,val):
        '''
        Always writes through to MBUS
        '''
        this.log.debug("fored-write: " + str(key) + ':' + str(val))
        addr = key[0]
        size = key[1]
        assert( isinstance(addr, int))
        assert( isinstance(val, int))
        this.mbus.write_mem(addr,val,size)



#
class RegFile(Memory):
    '''
    Allows dictionary-like access to the M3's registers
    '''
    
    #
    def __init__(this, mbus, base_addr, writeback=False, \
                                        log_level = logging.WARN):
        '''
        note: base_addr will need to be updated every time
        '''
        super( RegFile, this).__init__(mbus)
        this.base_addr = base_addr 

        this.log = m3_logging.getLogger( type(this).__name__)
        this.log.setLevel(log_level)
        
        # specific ordering matching on-board gdb code
        this.names = [  'isr_lr', 'sp', 'r8', 'r9', 'r10', 'r11', 
                        'r4', 'r5', 'r6', 'r7', 'r0', 'r1', 'r2', 
                        'r3', 'r12', 'lr', 'pc', 'xpsr', ]
        this.trans_names = { 'r13': 'sp', 'r14':'lr', 'r15':'pc'}
        # The M0 does not include floating-point registers
        this.warn_names = [ 'f0', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 
                            'fps', ]
        this.warn_trans_names = { 'cpsr':'xpsr' }                            
        this.offsets = dict( zip(this.names, 
                            range(0, 4* len(this.names), 4))
                          )
        this.writeback = writeback
        this.local =  {}                                
        
    #
    def update_base_addr(this, base_addr):
        '''
        used to update the base pointer of the register file
        '''
        this.log.debug('Update Base Addr: ' + hex(base_addr))
        this.base_addr = base_addr
        this.local = {} # clear local reg cache

    #
    def __getitem__(this,key):
        # just pretend all fp regs are zero
        if key in this.warn_names:
            this.log.warn('Reading: ' + str(key) + ' as 0')
            return 0
        elif key in this.warn_trans_names:
            this.log.warning('Reading ' + str(this.warn_trans_names[key]) + \
                            ' in place of ' + str(key))
            key = this.warn_trans_names[key] 
        elif key in this.trans_names:
            key = this.trans_names[key]

        assert( key in this.names)
        assert(this.base_addr != None)
        mem_addr = this.base_addr + this.offsets[key]
        val = this.mbus.read_mem(mem_addr,32)
        # ARM pc reads return pc + 4 (it's wierd)
        if key == 'pc': 
            val += 4
            this.log.debug("RegRd: pc(+4) " + hex(val))
        else: 
            this.log.debug("RegRd: " + str(key) + " " + hex(val))
        return val

    #
    def __setitem__(this,key,val):
        '''
        note: by default this only caches updates locally
        '''
        this.log.debug("RegWr: " + str(key) + ':' + hex(val))

        if key in this.warn_names:
            this.log.warn('Writing: ' + str(key) + ' as 0')
            return 0
        elif key in this.warn_trans_names:
            this.log.warning('Writing' + str(this.warn_trans_names[key]) + \
                            ' in place of ' + str(key))
            key = this.warn_trans_names[key] 
        elif key in this.trans_names:
            key = this.trans_names[key]

        assert( key in this.names)
        assert( isinstance(val, int))

        if (this.writeback):
            assert(this.base_addr != None)
            mem_addr = this.base_addr + this.offsets[key]
            this.mbus.write_mem(mem_addr,val,32)
        else: 
            this.local[key] = val

    #
    def forceWrite(this,key,val):
        '''
        Always writes through to MBUS
        '''
        this.log.debug("fored-write: " + str(key) + ':' + str(val))
        assert( key in this.names)
        mem_addr = this.base_addr + this.offsets[key]
        this.mbus.write_mem(mem_addr,val,32)

    #
    def getLocal(this, key):
        if key in this.local:
            if key == 'pc': #ARM pc reg is wierd
                return this.local[key] + 4
            else:
                return this.local[key]
        else: return None


class mbus_controller( object):

    TITLE = "MBUS Programmer"
    DESCRIPTION = "Tool to program M3 chips using the MBUS protocol."
    DEFAULT_PRC_PREFIX = '0x1'

    #MSG_TYPE = 'b+'

    def __init__(self, m3_ice, parser):
        self.m3_ice = m3_ice
        self.parser = parser
        self.add_parse_args(parser)

    def add_parse_args(self, parser):


        self.subparsers = parser.add_subparsers(
                title = 'MBUS Commands',
                description='MBUS Actions supported through ICE',
                )

        self.parser_program = self.subparsers.add_parser('program',
                help = 'Program the PRC via MBUS')
        self.parser_program.add_argument('-p', '--short-prefix',
                help="The short MBUS address of the PRC, e.g. 0x1",
                default=mbus_controller.DEFAULT_PRC_PREFIX,
                )
        self.parser_program.add_argument('BINFILE', 
                help="Program to flash over MBUS",
                )
        self.parser_program.set_defaults(func=self.cmd_program)

        self.parser_gdb = self.subparsers.add_parser('gdb',
                help = 'Debug the PRC via GDB')
        self.parser_gdb.add_argument('-p', '--short-prefix',
                help="The short MBUS address of the PRC, e.g. 0x1",
                default=mbus_controller.DEFAULT_PRC_PREFIX,
                )
        self.parser_gdb.add_argument('--port',
                help="The TCP port GDBServer should bind to",
                default='10001'
                )
        self.parser_gdb.add_argument('--input-mode',
                help="Where should we look for input: \n"\
                     "'gdb': start a gdbserver remote on --port,\n"
                     "'direct': accept gdbserver commands directly from stdin",
                default='gdb'
                )

        self.parser_gdb.set_defaults(func=self.cmd_gdb)


    def cmd_program(self):
        '''
        Programs the PRC over MBUS
        '''

        try:
            ice = self.m3_ice.ice
            status = list( map( lambda x: ice.power_get_onoff(x), 
                [ ice.POWER_0P6, ice.POWER_1P2, ice.POWER_VBATT ]))
            status =  list( map( lambda x:  "On" if x==True else "Off", status)) 
            status = ','.join(status)
            print (" Current Voltages are: " + str(status))
            
            if status == 'On,On,On':
                self.m3_ice.dont_do_default("Run power-on sequence", 
                        self.m3_ice.power_on)
                self.m3_ice.dont_do_default("Reset M3", self.m3_ice.reset_m3)
            else: raise Exception()
        except: 
            self.m3_ice.do_default("Run power-on sequence", 
                    self.m3_ice.power_on)
            self.m3_ice.do_default("Reset M3", self.m3_ice.reset_m3)


        logger.info("** Setting ICE MBus controller to slave mode")
        self.m3_ice.ice.mbus_set_master_onoff(False)

        logger.info("** Disabling ICE MBus snoop mode")
        self.m3_ice.ice.mbus_set_snoop(False)

        logger.info("")

        #logger.info("Triggering MBUS internal reset")
        #self.m3_ice.ice.mbus_set_internal_reset(True)
        #self.m3_ice.ice.mbus_set_internal_reset(False)

        #pull prc_addr from command line
        # and convert to binary
        prc_addr = int(self.m3_ice.args.short_prefix, 16)

        if (prc_addr > 0x0 and prc_addr < 0xf):
            mbus_short_addr = (prc_addr << 4 | 0x02)
            mbus_addr = struct.pack(">I", mbus_short_addr)
        elif (prc_addr >= 0xf0000 and prc_addr < 0xfffff):
            raise Exception("Only short prefixes supported")
            #mbus_addr = struct.pack(">I", mbus_long_addr)
        else: raise Exception("Bad MBUS Addr")

        logger.debug('MBus_PRC_Addr: ' + binascii.hexlify(mbus_addr))

        # 0x0 = mbus register write
        mbus_regwr = struct.pack(">I", ( prc_addr << 4) | 0x0 ) 
        # 0x2 = memory write
        mbus_memwr = struct.pack(">I", ( prc_addr << 4) | 0x2 ) 

        # number of bytes per packet (must be < 256)
        chunk_size_bytes = 128 
        # actual binfile is hex characters (1/2 byte), so twice size
        chunk_size_chars = chunk_size_bytes * 2

        ## lower CPU reset 
        ## This won't work until PRCv16+
            #RUN_CPU = 0xA0000040  # Taken from PRCv14_PREv14.pdf page 19. 
            #mem_addr = struct.pack(">I", RUN_CPU) 
        # instead use the RUN_CPU MBUS register
        data= struct.pack(">I", 0x10000000) 
        logger.info("raising RESET signal... ")
        self.m3_ice.ice.mbus_send(mbus_regwr, data)

        # load the program
        logger.info( 'writing binfile: '  + self.m3_ice.args.BINFILE) 
        datafile = self.m3_ice.read_binfile_static(self.m3_ice.args.BINFILE)
        # convert to hex
        datafile = binascii.unhexlify(datafile)
        # then switch endian-ness
        # https://docs.python.org/2/library/struct.html
        bigE= '>' +  str(int(len(datafile)/4)) + 'I' # words = bytes/4
        litE= '<' + str(int(len(datafile)/4)) + 'I' 
        # unpack little endian, repack big endian
        datafile = struct.pack(bigE, * struct.unpack(litE, datafile))
 
        # split file into chunks, pair each chunk with an address, 
        # then write each addr,chunk over mbus
        logger.debug ( 'splitting binfile into ' + str(chunk_size_bytes) 
                            + ' byte chunks')
        payload_chunks = [ datafile[i:i+chunk_size_bytes] for i in \
                        range(0, len(datafile), chunk_size_bytes) ]
        payload_addrs = range(0, len(datafile), chunk_size_bytes) 

        for mem_addr, payload in zip(payload_addrs, payload_chunks):

            mem_addr = struct.pack(">I", mem_addr)
            logger.debug('Mem Addr: ' + binascii.hexlify(mem_addr))

            logger.debug('Payload: ' + binascii.hexlify(payload))

            data = mem_addr + payload 
            #logger.debug( 'data: ' + binascii.hexlify(data ))
            logger.debug("Sending Packet... ")
            self.m3_ice.ice.mbus_send(mbus_memwr, data)

        time.sleep(0.1)


        # @TODO: add code here to verify the write? 

        # see above, just using RUN_CPU MBUS register again
        clear_data= struct.pack(">I", 0x10000001)  # 1 clears reset
        logger.info("clearing RESET signal... ")
        self.m3_ice.ice.mbus_send(mbus_regwr, clear_data)
 
        logger.info("")
        logger.info("Programming complete.")
        logger.info("")

        return 
    



   
    #
    #
    #

    #
    #
    #
    def cmd_gdb(self):
      
        class InputManager(object):
            '''
            Alternate frontend that skips GDBserv and 
            processes commands straight from stdin
            '''
            def run(this): pass
            def get(this):
                s = raw_input("<: ")
                if len(s) == 1:
                    return s[0], (), {}
                elif s[0] == '_':
                    return s, (), {}
                else:
                    return s[0], (s[1:]), {}
            def put(this, msg):
                print (">: " + str(msg) )

        #pull prc_addr from command line
        # and convert to binary
        prc_addr = int(self.m3_ice.args.short_prefix, 16)
   
        #determin current logging level
        dbgLvl = logger.getEffectiveLevel()

        #parse command line args
        port =  int(self.m3_ice.args.port)
        input_mode = self.m3_ice.args.input_mode.lower()

        from . import m3_gdb

        if input_mode == 'gdb':
            # gdb interface
            interface= m3_gdb.GdbRemote(tcp_port = port , log_level = dbgLvl )
        elif input_mode == 'direct':
            interface = InputManager()
        else: raise Exception('Unsupported input_mode' + \
                        str(self.m3_ice.args.input_mode) )

        # create GDB controller backend 
        ctrl = m3_gdb.GdbCtrl( self.m3_ice.ice, interface, prc_addr, \
                            log_level = dbgLvl)

        logger.debug ("GDB CTRL main loop")
        interface.run()                                        
        while (True):
            cmd, args, kwargs = interface.get()
            cmd = 'cmd_'+cmd

            if cmd == 'cmd__quit_': 
                logger.info('GDB CTRL Quiting')
                break
            else : 
                func = getattr(ctrl, cmd)
                ret = func(*args, **kwargs)
                if ret != None: interface.put(ret)

