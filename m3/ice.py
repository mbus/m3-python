#!/usr/bin/env python

################################################################################

# Coerce Py2k to act more like Py3k
from __future__ import (absolute_import, division, print_function, unicode_literals)
from builtins import (
        ascii, bytes, chr, dict, filter, hex, input, int, isinstance, list, map,
        next, object, oct, open, pow, range, round, str, super, zip,
        )

import binascii
from copy import copy
from copy import deepcopy
import errno
import functools
import socket
import struct
import sys
import time
import os

from . import m3_logging
logger = m3_logging.getLogger(__name__)

try:
    import threading
    import queue as Queue
except ImportError:
    logger.warn("Your python installation does not support threads.")
    logger.warn("")
    logger.warn("Please install a version of python that supports threading.")
    raise

try:
    import serial
except ImportError:
    logger.warn("You do not have the pyserial library installed.")
    logger.warn("")
    logger.warn("For debian-based systems (e.g. Ubuntu):")
    logger.warn("\tsudo apt-get install pyserial")
    logger.warn("For rpm-based systems (e.g. Red Hat):")
    logger.warn("\tsudo yum install pyserial")
    logger.warn("For more installation instructions + see:")
    logger.warn("\thttp://pyserial.sourceforge.net/pyserial.html#installation")
    raise

################################################################################

class ICE(object):
    VERSIONS = ((0,1),(0,2),(0,3),(0,4),(0,5))
    ONEYEAR = 365 * 24 * 60 * 60

    class ICE_Error(Exception):
        '''
        A base class for all exceptions raised by this module
        '''
        pass

    class NotConnectedError(ICE_Error):
        '''
        A method was called that requires ICE to be connected when it is not.
        '''
        pass

    class FormatError(ICE_Error):
        '''
        Something in the ICE protocol communicating with the board went wrong.

        This error should never be encountered in normal use.
        '''
        pass

    class ParameterError(ICE_Error):
        '''
        An illegal parameter was passed.

        This may be raised by the ICE library if it can determine in advance
        that the reqeust is illegal (e.g. out of range), or by the ICE board if
        the board rejects the desired setting (e.g. not configurable)
        '''

    class NAK_Error(ICE_Error):
        '''
        Raised when an unexpected NAK is returned
        '''
        pass

    class VersionError(ICE_Error):
        '''
        A method was called that attached ICE version does not support.
        '''
        def __init__(self, required_version, current_version):
            self.required_version = required_version
            self.current_version = current_version
            super(ICE.VersionError, self).__init__()

    class CapabilityError(ICE_Error):
        '''
        A method was called that the attached ICE board does not have hardware
        frontend for.
        '''
        def __init__(self, required_capability, capabilities):
            self.required_capability = required_capability
            self.capabilities = capabilities
            super(ICE.CapabilityError, self).__init__()

    class TimeoutError(ICE_Error):
        ''' 
        A method was called that attempted to read more bytes than the Serial
        interface had available within the timeout window
        '''
        def __init__(self, _timeout, _partial_data):
            self.timeout = _timeout
            self.partial_data = _partial_data
            super(ICE.TimeoutError, self).__init__()

    ## Support decorators:
    def min_proto_version(version):
        '''
        Decorator for library calls that verifies the requested call is
        supported by the protocol version negotiated by the current ICE board.
        '''
        def wrapped_fn_factory(fn_being_decorated):
            @functools.wraps(fn_being_decorated)
            def wrapped_fn(self, *args, **kwargs):
                if not hasattr(self, "minor"):
                    raise self.NotConnectedError("ICE must be connected first ({})".format(fn_being_decorated))
                major, minor = map(int, version.split('.'))
                if major != 0:
                    raise self.ICE_Error("Major version bump?")
                if self.minor < minor:
                    raise self.VersionError(minor, self.minor)
                return fn_being_decorated(self, *args, **kwargs)
            return wrapped_fn
        return wrapped_fn_factory

    def max_proto_version(version):
        '''
        Decorator for library calls that verifies the requested call is
        supported by the protocol version negotiated by the current ICE board.
        '''
        def wrapped_fn_factory(fn_being_decorated):
            @functools.wraps(fn_being_decorated)
            def wrapped_fn(self, *args, **kwargs):
                if not hasattr(self, "minor"):
                    raise self.ICE_Error("ICE must be connected first")
                major, minor = map(int, version.split('.'))
                if major != 0:
                    raise self.ICE_Error("Major version bump?")
                if self.minor > minor:
                    raise self.VersionError(minor, self.minor)
                return fn_being_decorated(self, *args, **kwargs)
            return wrapped_fn
        return wrapped_fn_factory

    def capability(cap):
        '''
        Decorator for library calls that verifies the requested call is
        supported by the capabilities reported by the current ICE board.
        '''
        def wrapped_fn_factory(fn_being_decorated):
            @functools.wraps(fn_being_decorated)
            def wrapped_fn(self, *args, **kwargs):
                try:
                    if cap not in self.capabilities:
                        raise self.CapabilityError(cap, self.capabilities)
                except AttributeError:
                    if 'ice_query_capabilities' not in fn_being_decorated.__name__:
                        if self.minor != 1:
                            logger.error("Version decorator must precede capability")
                            raise
                return fn_being_decorated(self, *args, **kwargs)
            return wrapped_fn
        return wrapped_fn_factory

    def __init__(self):
        '''
        An ICE object.

        Most methods are not usuable until connect() has been called.
        '''

        self.event_id = 0
        self.last_event_id = -1
        self.sync_queue = Queue.Queue(1)

        self.msg_handler = {}
        self.d_lock = threading.Lock()
        self.d_frag = bytes()
        self.msg_handler['d'] = self.d_defragger
        self.b_lock = threading.Lock()
        self.b_frag = bytes()
        self.msg_handler['b'] = self.b_defragger
        self.B_lock = threading.Lock()
        self.B_frag = bytes()
        self.msg_handler['B'] = self.B_defragger
        self.B_formatter_success_only = False
        self.B_formatter_control_bits = False
        self.msg_handler['B+'] = self.B_formatter
        self.msg_handler['b+'] = self.b_formatter

        self.msg_handler['e'] = self.e_handler

        self.goc_ein_toggle = -1

        # Set initial, minimal capability set
        self.capabilities = 'VvXx'

    def find_baud(self, serial_device):
       
        # we're only trying these
        baudrates = [  115200, 2000000] 
        version_request = binascii.unhexlify('560000')
        found = False
        
        with serial.Serial(serial_device, baudrates[0], 
                    timeout=0.05 ) as tmpSerial:

            if not tmpSerial.isOpen():
                raise self.ICE_Error("Failed to connect to temporary serial device")

            for baudrate in baudrates:

                logger.debug('Trying baudrate: ' + str(baudrate))
                try:
                    tmpSerial.baudrate = baudrate
                except IOError: 
                    logger.debug("Error changing baudrate, assuming socat port")
                    found = baudrate
                    break
                       
                # send a version request and see what happens
                tmpSerial.write(version_request)
                rxBytes = tmpSerial.read(5) #see if this times out
                if len(rxBytes) != 0: 
                    found = baudrate
                    break
            
        if not found:
            raise Exception("Unable to determine baudrate!")

        logger.debug ("Found Baudrate: " + str(baudrate) )
        return found 

    def connect(self, serial_device, baudrate=115200):
        '''
        Opens a connection to the ICE board.

        The ICE object configuration (e.g. message handlers) cannot be safely
        changed after this method is invoked.
        '''

        #500ms timeout for serial to help catch runaway packets
        # cygwin cannot support 5ms or 50ms timeouts 
        # m3_ice_sim doesn't support baudrate
        try:
            self.dev = serial.Serial(serial_device, baudrate, timeout=0.5)
        except IOError:
            logger.warn("Skipping baudrate?")
            self.dev = serial.Serial(serial_device, timeout=0.5)

        if self.dev.isOpen():
            logger.info("Connected to serial device at " + self.dev.portstr + 
                " at " + str(baudrate) + " baud")
        else:
            raise self.ICE_Error("Failed to connect to serial device")

        self.communicator_stop_request = threading.Event()
        self.communicator_stop_response = threading.Event()
        self.comm_thread = threading.Thread(target=self.communicator)
        self.comm_thread.daemon = True
        self.comm_thread.start()

        self.negotiate_version()

        if self.minor == 2:
            # V2 ICE sets GOC on by default, which is annoying. Correct that.
            self.goc_set_onoff(False)

    def is_connected(self):
        return hasattr(self, 'dev')

    def destroy(self):
        if hasattr(self, 'dev'):
            self.communicator_stop_request.set()
            self.communicator_stop_response.wait()
            self.dev.close()
            logger.info("Connection to " + self.dev.portstr + " closed.")
            del(self.dev)

    def spawn_handler(self, msg_type, event_id, length, msg):
        try:
            handler = self.msg_handler[msg_type]
            #t = threading.Thread(target=self.msg_handler[msg_type],
            #        args=(msg_type, event_id, length, msg))
            #t.daemon = True
            #t.start()
        except KeyError:
            if msg_type not in self.capabilities:
                logger.warn("Synchronization lost. Likely causes:")
                logger.warn("  - you are reconnecting to an ICE that was previously snooping")
                logger.warn("  - your computer can't keep up with the rate of messages ICE sends")
                logger.warn("  - some transient serial error occurred (not impossible at 3 MBaud)")
                logger.warn("This library will try to get back on track, but if")
                logger.warn("this message keeps printing, you'll need to hit the")
                logger.warn("reset button on the ICE board")
                # The idea here is to read as much as available in the serial
                # buffer, throwing it away, and count on the gaps between ICE
                # messages to get things back on track. A bit ugly, but I'm not
                # sure I know of a better solution :/
                self.dev.read()
            try:
                logger.warn("WARNING: No handler registered for message type: " +
                        str(msg_type))
                logger.warn("Known Types:")
                for t,f in self.msg_handler.iteritems():
                    logger.warn("%s\t%s" % (t, str(f)))
                logger.warn("         Dropping packet:")
                logger.warn("")
                logger.warn("    Type: %s" % (msg_type))
                logger.warn("Event ID: %d" % (event_id))
                logger.warn("  Length: %d" % (length))
                logger.warn(" Message:" + msg.encode('hex'))
            except Exception as e:
                logger.warn("Unhandled exception trying to report unknown message.")
                logger.warn(str(e))
                logger.warn("Suppressed.")
            return
        try:
            handler(msg_type, event_id, length, msg)
        except self.NotConnectedError:
            # The ICE board can send async messages before the library is set up
            # to receive them, silently drop
            logger.debug("Received message from ICE before connection established, dropping.")
            try:
                logger.debug("         Dropping packet:")
                logger.debug("")
                logger.debug("    Type: %s" % (msg_type))
                logger.debug("Event ID: %d" % (event_id))
                logger.debug("  Length: %d" % (length))
                logger.debug(" Message:" + msg.encode('hex'))
            except Exception as e:
                logger.debug("Unhandled exception trying to report unknown message.")
                logger.debug(str(e))
                logger.debug("Suppressed.")

    def useful_read(self, length, check_timeout = False):
        rxBuf = b''
        while len(rxBuf) < length:
            rx = self.dev.read(length - len(rxBuf))

            if check_timeout and len(rx) == 0: #timeout occured
                raise self.TimeoutError(self.dev.timeout, rxBuf)

            else: # add to the buffer
                rxBuf += rx
    
        assert len(rxBuf) == length
        logger.debug('Raw Read: ' + binascii.hexlify(rxBuf) )
        return rxBuf 
       
    def communicator(self):
        while not self.communicator_stop_request.isSet():
            try:
                # Read has a timeout of .1 s. Polling is the easiest way to
                # do x-platform cancellation
                msg_type, event_id, length = self.useful_read(3)
            except ValueError:
                continue
            except (serial.SerialException, OSError):
                break
            msg_type = ord(msg_type)
            event_id = ord(event_id)
            length = ord(length)
            #print("Got msg type", msg_type, chr(msg_type), length)
            try:
                msg = self.useful_read(length, check_timeout = True)
            except self.TimeoutError:
                logger.warn("Timeout error occured, skipping rest of packet!")
                continue
            #print(msg.encode('hex'))

            if event_id == self.last_event_id:
                logger.warn("WARNING: Duplicate event_id! THIS IS A BUG [somewhere]!!")
                logger.warn("         Dropping packet:")
                logger.warn("")
                logger.warn("    Type: %d" % (msg_type))
                logger.warn("Event ID: %d" % (event_id))
                logger.warn("  Length: %d" % (length))
                logger.warn(" Message:" + msg.encode('hex'))
            else:
                self.last_event_id = event_id

            if msg_type in (0,1):
                # Ack / Nack response from a synchronous message
                try:
                    if msg_type == 0:
                        logger.debug("Got an ACK packet. Event: " + str(event_id))
                    else:
                        logger.info("Got a NAK packet. Event:" + str(event_id))
                    self.sync_queue.put((msg_type, msg))
                except Queue.Full:
                    logger.warn("WARNING: Synchronization lost. Unsolicited ACK/NAK.")
                    logger.warn("         Dropping packet:")
                    logger.warn("")
                    logger.warn("    Type: %s" % (["ACK","NAK"][msg_type]))
                    logger.warn("Event ID: %d" % (event_id))
                    logger.warn("  Length: %d" % (length))
                    logger.warn(" Message:" + msg.encode('hex'))
            else:
                msg_type = chr(msg_type)
                logger.debug("Got an async message of type: " + msg_type)
                self.spawn_handler(msg_type, event_id, length, msg)
        self.communicator_stop_response.set()
        if hasattr(self, 'on_disconnect'):
            self.on_disconnect()

    def string_to_masks(self, mask_string):
        ones = 0
        zeros = 0
        mask_string = mask_string.replace(' ','')
        idx = len(mask_string)
        for c in mask_string:
            idx -= 1
            if c == '1':
                ones |= (1 << idx)
            elif c == '0':
                zeros |= (1 << idx)
            elif c in ('x', 'X'):
                continue
            else:
                raise self.FormatError("Illegal character: >>>" + c + "<<<")
        return ones,zeros

    def masks_to_strings(self, ones, zeros, length):
        s = ''
        for l in range(length):
            o = bool(ones & (1 << l))
            z = bool(zeros & (1 << l))
            if o and z:
                raise self.FormatError("masks_to_strings has req 1 and req 0." +
                        "ones {} zeros {} length {} l {}".format(ones, zeros, length, l))
            if o:
                s = '1' + s
            elif z:
                s = '0' + s
            else:
                s = 'x' + s
        return s

    def d_defragger(self, msg_type, event_id, length, msg):
        '''
        Helper function to defragment 'd' type I2C messages before forwarding.

        This helper is installed by default for 'd' messages. It will attempt to
        call a helper registered under the name 'd+' when a complete message has
        been received. The message will be assigned the event id of the last
        received fragment.

        It may be safely overridden.
        '''
        with self.d_lock:
            assert msg_type == 'd'
            self.d_frag += msg
            # XXX: Make version dependent
            if length != 255:
                sys.stdout.flush()
                logger.debug("Got a complete I2C transaction of length %d bytes. Forwarding..." % (len(self.d_frag)))
                sys.stdout.flush()
                self.spawn_handler('d+', event_id, len(self.d_frag), \
                        deepcopy(self.d_frag))
                self.d_frag = bytes()
            else:
                logger.debug("Got an I2C fragment... thus far %d bytes received:" % (len(self.d_frag)))

    @min_proto_version("0.2")
    def b_defragger(self, msg_type, event_id, length, msg):
        '''
        Helper function to defragment 'b' type MBus messages before forwarding.

        This helper is installed by default for 'b' messages. It will attempt to
        call a helper registered under the name 'b+' when a complete message has
        been received. The message will be assigned the event id of the last
        received fragment.

        It may be safely overridden.
        '''
        with self.b_lock:
            logger.debug("\tmsg_type: %s, event_id: %s, length: %s, msg: %s"
                    % (msg_type, event_id, length, repr(msg)))
            assert msg_type == 'b'
            self.b_frag += msg
            # XXX: Make version dependent
            if length != 255:
                logger.debug("Got a complete MBus message of length %d bytes. Forwarding..." % (len(self.b_frag)))
                self.spawn_handler('b+', event_id, len(self.b_frag), \
                        deepcopy(self.b_frag))
                self.b_frag = bytes()
            else:
                logger.debug("Got a MBus fragment... thus far %d bytes received:" % (len(self.b_frag)))

    @min_proto_version("0.2")
    def B_defragger(self, msg_type, event_id, length, msg):
        '''
        Helper function to defragment 'B' type snooped MBus messages before forwarding.

        This helper is installed by default for 'B' messages. It will attempt to
        call a helper registered under the name 'B+' when a complete message has
        been received. The message will be assigned the event id of the last
        received fragment.

        It may be safely overridden.
        '''
        with self.B_lock:
            assert msg_type == 'B'
            self.B_frag += msg
            # XXX: Make version dependent
            if length != 255:
                sys.stdout.flush()
                logger.debug("Got a complete snooped MBus message. Length %d bytes. Forwarding..." % (len(self.B_frag)))
                sys.stdout.flush()
                self.spawn_handler('B+', event_id, len(self.B_frag), \
                        deepcopy(self.B_frag))
                self.B_frag = bytes()
            else:
                logger.debug("Got a snoop MBus fragment... thus far %d bytes received:" % (len(self.B_frag)))

    @min_proto_version("0.2")
    def B_formatter(self, msg_type, event_id, length, msg):
        return self.common_bB_formatter(msg_type, event_id, length, msg, 'B++')

    @min_proto_version("0.2")
    def b_formatter(self, msg_type, event_id, length, msg):
        return self.common_bB_formatter(msg_type, event_id, length, msg, 'b++')

    @min_proto_version("0.2")
    def common_bB_formatter(self, msg_type, event_id, length, msg, b_type):
        '''
        Helper function that parses 'B+' snooped MBus messages before forwarding.

        This helper is installed by default for 'B+' messages. It will attempt
        to call a helper registered under the name 'B++' when a complete message
        has been received. B++ messages do not have the standard signature,
        instead they expect a callback of the form:

            Bpp_callback(address, data)
              or
            Bpp_callback(address, data, control_bit_0, control_bit_1)

        The member variable "B_formatter_success_only" (default False) controls
        whether all messages are forwarded or only messages that were ACK'd.

        The member variable "B_formatter_control_bits" (default False) controls
        whether the control bits are sent.

        This function may be safely overridden.
        '''
        addr = msg[0:4]
        data = msg[4:-1]
        cb = ord(msg[-1:])
        # status_bits <= `SD status_bits | {4'b0000, mbus_rxfail, mbus_rxbcast, ice_export_control_bits};
        cb0 = bool(cb & 0x1)
        cb1 = bool(cb & 0x2)
        success = cb0 & (~cb1) # XXX Something is wrong here [also fix default]
        try:
            handler = self.msg_handler[b_type]
        except KeyError:
            logger.warn("All registered handlers: {}".format(self.msg_handler))
            logger.warn("Looking up key >>{}<<".format(b_type))
            try:
                logger.warn("No handler registered for B++ (formatted, snooped MBus) messages")
                logger.warn("Dropping message:")
                logger.warn("\taddr: " + binascii.hexlify(addr))
                logger.warn("\tdata: " + binascii.hexlify(data))
                logger.warn("\tstat: " + binascii.hexlify(cb))
                logger.warn("")
            except Exception as e:
                logger.warn("Unhandled exception trying to report missing B++ handler.")
                logger.warn(str(e))
                logger.warn("Suppressed.")
            return
        if self.B_formatter_control_bits:
            handler(addr, data, cb0, cb1)
        else:
            handler(addr, data)

    @min_proto_version("0.4")
    def e_handler(self, msg_type, event_id, length, msg):
        '''
        Helper function for the 'e' command, exit
        '''
        logger.info('Caught "e" command, shutting down')
        #sys.exit(0)
        os._exit( int(binascii.hexlify(msg) ,16) )

    def send_message(self, msg_type, msg='', length=None):
        if type(msg_type) != bytes:
            msg_type = bytes(msg_type, 'utf-8')

        if len(msg_type) != 1:
            raise self.FormatError("msg_type must be exactly 1 byte")

        if type(msg) != bytes:
            msg = bytes(msg, 'utf-8')

        if len(msg) > 255:
            raise self.FormatError("msg too long. Maximum msg is 255 bytes")

        if length is None:
            length = len(msg)

        buf = struct.pack("BBB", ord(msg_type), self.event_id, length)
        self.event_id = (self.event_id + 1) % 256
        logger.debug('Sending %s', binascii.hexlify(buf+msg))
        self.dev.write(buf + msg)

        # Ugly hack so python allows keyboard interrupts
        return self.sync_queue.get(True, self.ONEYEAR)

    def send_message_until_acked(self, msg_type, msg='', length=None, tries=5):
        while tries:
            ack, msg = self.send_message(msg_type, msg, length)
            if ack == 0:
                return msg
            tries -= 1

        raise self.NAK_Error

    def negotiate_version(self):
        '''
        Establish communication with an ICE board.

        This function is called automatically by __init__ and should not be
        called directly. For ICE versions >= 0.2, this function automatically
        calls ice_query_capabilities and sets up the ICE library appropriately.
        '''
        logger.info("This library supports versions...")
        for major, minor in ICE.VERSIONS:
            logger.info("\t%d.%d" % (major, minor))

        logger.debug("Sending version probe")
        resp = self.send_message_until_acked('V')
        if (len(resp) is 0) or (len(resp) % 2):
            raise self.FormatError("Version response: " + resp)

        logger.info("This ICE board supports versions...")
        self.major = None
        self.minor = None
        while len(resp) > 0:
            major, minor = struct.unpack("BB", resp[:2])
            resp = resp[2:]
            if self.major is None and (major, minor) in ICE.VERSIONS:
                self.major = major
                self.minor = minor
                logger.info("\t%d.%d **Chosen version" % (major, minor))
            else:
                logger.info("\t%d.%d" % (major, minor))

        if self.major is None:
            logger.warn("No versions in common. Version negotiation failed.")
            raise self.ICE_Error

        if self.major != 0:
            logger.error("Major version number bump. Need to re-examine python versioning")
            raise self.ICE_Error

        self.send_message_until_acked('v', struct.pack("BB", self.major, self.minor))

        if self.minor >= 2:
            logger.debug("ICE version supports capabilities, querying")
            self.capabilities = 'VvXx?'
            self.ice_query_capabilities()
            logger.debug("Capabilities: " + self.capabilities)
        else:
            self.capabilities = 'VvXxdIifOoGgPp'
            logger.debug("Version 0.1 does not have capability support, skipping")

    def min_version(self, required_version):
        if required_version > 1:
            logger.error("Need to fix this versioning system. Major version number bumped")
            raise self.ICE_Error
        required_version = int(required_version * 10)
        try:
            if self.minor < required_version:
                raise self.VersionError(required_version, self.minor)
        except AttributeError:
            logger.error("Attempt to call method before version negotiation?")
            raise

    def _fragment_sender(self, msg_type, msg):
        '''
        Internal. (helper for {i2c,goc,ein,mbus}_send)
        '''
        # XXX: Make version dependent?
        FRAG_SIZE = 255
        retry = True 

        sent = 0
        logger.debug("Sending %d byte message (in %d byte fragments)" % \
                                                    (len(msg), FRAG_SIZE))
        while len(msg) >= FRAG_SIZE:
            ack,resp = self.send_message(msg_type, msg[0:FRAG_SIZE])
            if ack == 1: # (NAK)
                if len(resp) == 0 and retry:
                    logger.warning("ICE NAK'd request to send with no length "\
                                    "sent field, assuming 0 and retrying")
                    retry = False
                else: return sent + ord(resp)
            else: retry = True

            msg = msg[FRAG_SIZE:]
            sent += FRAG_SIZE
            logger.debug("\tSent %d byte s, %d remaining" % (sent, len(msg)))

        logger.debug("Sending last message fragment, %d bytes long" % \
                                                            (len(msg)))
        while True:
            ack,resp = self.send_message(msg_type, msg)
            if ack == 1:
                if len(resp) == 0 and retry:
                    logger.warning("ICE NAK'd request to send with no length "\
                                    "sent field, assuming 0 and retrying")
                    retry = False
                else: return sent + ord(resp)
            else: break # no more while loop
        sent += len(msg)
        return sent

    ## QUERY / CONFIGURE ICE ##
    @min_proto_version("0.2")
    @capability('?')
    def ice_query_capabilities(self):
        '''
        Queries ICE board for available hardware frontends.

        The ICE library will be configured to raise an ICE.CapabilityError
        if a request that is unsupported by this hardware is requested.

        This interface is very raw and needs to be wrapped in something more
        user-friendly and library-esque. It currently returns the raw array of
        characters from the ICE board, which requires the caller to know the
        ICE protocol.
        '''
        resp = self.send_message_until_acked('?', struct.pack("B", ord('?')))
        self.capabilities = resp
        return resp

    @min_proto_version("0.2")
    @capability('?')
    def ice_get_baudrate(self):
        '''
        Gets the current baud rate of the ICE bridge in Hz.

        XXX: Returns the ideal value, not the exact speed. Not sure which is
        more correct / more useful.
        '''
        self.min_version(0.2)
        resp = self.send_message_until_acked('?', struct.pack("B", ord('b')))
        div = struct.unpack("!H", resp)[0]

        if div == 0x00AE:
            return 1152200
        elif div == 0x0007:
            return 3000000
        else:
            raise self.FormatError("Unknown baud divider?")

    @min_proto_version("0.2")
    @capability('_')
    def ice_set_baudrate(self, div, baudrate):
        '''
        Sets a new baud rate for the ICE bridge.

        Internal. This function is not meant to be called directly.
        '''
        self.min_version(0.2)
        self.send_message_until_acked('_', struct.pack("!BH", ord('b'), div))
        try:
            self.dev.baudrate = baudrate
        except IOError as e:
            if e.errno == 25:
                logger.warn("Failed to set baud rate (if socat, ignore)")
            else:
                raise

    @min_proto_version("0.2")
    @capability('_')
    def ice_set_baudrate_to_115200(self):
        self.ice_set_baudrate(0x00AE, 115200)

    @min_proto_version("0.2")
    @capability('_')
    def ice_set_baudrate_to_230400(self):
        self.ice_set_baudrate(0x00AE//2, 115200*2)

    @min_proto_version("0.2")
    @capability('_')
    def ice_set_baudrate_to_460800(self):
        self.ice_set_baudrate(0x00AE//4, 115200*4)

    @min_proto_version("0.2")
    @capability('_')
    def ice_set_baudrate_to_921600(self):
        self.ice_set_baudrate(0x00AE//8, 115200*8)

    @min_proto_version("0.2")
    @capability('_')
    def ice_set_baudrate_to_1843200(self):
        self.ice_set_baudrate(0x00AE//16, 115200*16)

    @min_proto_version("0.2")
    @capability('_')
    def ice_set_baudrate_to_2000000(self):
        self.ice_set_baudrate(0x000A, 2000000)

    @min_proto_version("0.2")
    @capability('_')
    def ice_set_baudrate_to_3_megabaud(self):
        self.ice_set_baudrate(0x0007, 3000000)


    ## GOC VS EIN HANDLING ##
    def get_goc_enabled(self):
        return self.goc_ein_toggle > 0

    def get_ein_enabled(self):
        return self.goc_ein_toggle == 0

    def set_goc_ein(self, goc=0, ein=0, goc_ir=0, restore_clock_freq=True):
        if ( (goc>0) + (ein>0) + (goc_ir>0) ) > 1:
            raise self.ICE_Error("Internal consistency goc vs ein failure")

        if self.minor == 1:
            if goc == 1:
                return
            else:
                raise self.ICE_Error("Attempt to call set_goc_ein for ein with protocol version 1")

        if ein:
            # Set to EIN mode
            if self.goc_ein_toggle == 0:
                # Already in ein mode, nothing to do
                return
            if self.goc_ein_toggle >= 1:
                # If we were set to GOC mode, capture the clock frequency
                self.goc_freq_divisor = self.goc_ein_get_freq_divisor()
            if restore_clock_freq:
                try:
                    self.goc_ein_set_freq_divisor(self.ein_freq_divisor)
                    logger.debug("Restored previous EIN clock frequency")
                except AttributeError:
                    self.goc_ein_set_freq_divisor(self.EIN_DEFAULT_DIVISOR)
                    logger.debug("Set EIN to default clock frequency")
            self.send_message_until_acked('o', struct.pack("BB", ord('p'), 0))
            self.goc_ein_toggle = 0
            logger.debug("Set goc/ein toggle to ein")
        elif (goc >= 1) or (goc_ir >= 1):
            assert( goc != goc_ir )

            # 1: visiable LED, 3: infrared LED
            goc_ctrl_byte = 1 if (goc>=1) else 3

            # Set to GOC mode
            if self.goc_ein_toggle == goc_ctrl_byte:
                return
            if self.goc_ein_toggle == 0:
                self.ein_freq_divisor = self.goc_ein_get_freq_divisor()
            if restore_clock_freq:
                try:
                    self.goc_ein_set_freq_divisor(self.goc_freq_divisor)
                    logger.debug("Restored previous GOC clock frequency")
                except AttributeError:
                    self.goc_ein_set_freq_divisor(self._goc_freq_in_hz_to_divisor(self.GOC_SPEED_DEFAULT_HZ))
                    logger.debug("Set GOC to default clock frequency")
            self.send_message_until_acked('o', 
                    struct.pack("BB", ord('p'), goc_ctrl_byte))
            self.goc_ein_toggle = goc_ctrl_byte
            logger.debug("Set goc/ein toggle to goc")
        else: raise Exception('Unsupported GOC/EIN mode')

    @max_proto_version("0.2")
    def goc_ein_get_freq_divisor_max_0_2(self):
        resp = self.send_message_until_acked('O', struct.pack("B", ord('c')))
        if len(resp) != 3:
            raise self.FormatError("Wrong response length from `Oc': " + str(resp))
        setting = struct.unpack("!I", "\x00"+resp)[0]
        return setting

    @min_proto_version("0.3")
    def goc_ein_get_freq_divisor_min_0_3(self):
        resp = self.send_message_until_acked('O', struct.pack("B", ord('c')))
        if len(resp) != 4:
            raise self.FormatError("Wrong response length from `Oc': " + str(resp))
        setting = struct.unpack("!I", resp)[0]
        logger.debug('got divisor value {}'.format(setting))
        return setting

    def goc_ein_get_freq_divisor(self):
        if self.minor > 2:
            return self.goc_ein_get_freq_divisor_min_0_3()
        else:
            return self.goc_ein_get_freq_divisor_max_0_2()

    @max_proto_version("0.2")
    def goc_ein_set_freq_divisor_max_0_2(self, divisor):
        packed = struct.pack("!I", divisor)
        if packed[0] != '\x00':
            raise self.ParameterError("Out of range.")
        msg = struct.pack("B", ord('c')) + packed[1:]
        self.send_message_until_acked('o', msg)

    @min_proto_version("0.3")
    def goc_ein_set_freq_divisor_min_0_3(self, divisor):
        logger.debug('set divisor to {}'.format(divisor))
        packed = struct.pack("!I", divisor)
        msg = struct.pack("B", ord('c')) + packed
        self.send_message_until_acked('o', msg)

    def goc_ein_set_freq_divisor(self, divisor):
        if self.minor > 2:
            return self.goc_ein_set_freq_divisor_min_0_3(divisor)
        else:
            return self.goc_ein_set_freq_divisor_max_0_2(divisor)

    ## GOC ##
    GOC_SPEED_DEFAULT_HZ = .625

    def _goc_display_delay(self, msg, event):
        try:
            freq = self.goc_freq
        except AttributeError:
            freq = ICE.GOC_SPEED_DEFAULT_HZ

        num_bits = len(msg) * 8
        t = num_bits / freq
        logger.info("Sleeping for %f seconds while it blinks..." % (t))
        while (t > 1):
            sys.stdout.write("\r\t\t\t\t\t\t")
            sys.stdout.write("\r\t%f remaining..." % (t))
            sys.stdout.flush()
            t -= 1
            if event.is_set():
                return
            time.sleep(1)
        time.sleep(t)

    @min_proto_version("0.1")
    @capability('f')
    def goc_send(self, msg, show_progress=True):
        '''
        Blinks a message via GOC.

        Takes a raw byte stream (e.g. binascii.unhexlify("aa")).
        Returns the number of bytes actually sent.

        Long messages may be fragmented between the ICE library and the ICE
        FPGA. These fragments will be combined on the ICE board, and given the
        significantly lower bandwidth of the GOC interface, there should be no
        interruption in message transmission.
        '''
        if not self.get_goc_enabled():
            self.set_goc_ein(goc=1)

        if show_progress:
            e = threading.Event()
            t = threading.Thread(target=self._goc_display_delay, args=(msg,e))
            t.daemon = True
            t.start()
            ret = self._fragment_sender('f', msg)
            e.set()
            t.join()
        else:
            ret = self._fragment_sender('f', msg)
        return ret

    @min_proto_version("0.1")
    @capability('O')
    def goc_get_frequency(self):
        '''
        Gets the GOC frequency.
        '''
        if not self.get_goc_enabled():
            self.set_goc_ein(goc=1)

        if self.minor == 3:
            logger.warn('ICE Firmware v0.3 reports wrong goc freq value.'\
                    ' Returning cached value.')
            try:
                return self.goc_freq
            except AttributeError:
                logger.warn('No cached value. Querying ICE. Value is junk')

        setting = self.goc_ein_get_freq_divisor()
        if self.minor == 1:
            NOMINAL = 2e6
        else:
            NOMINAL = 4e6
        freq_in_hz = NOMINAL / setting
        return freq_in_hz

    def _goc_freq_in_hz_to_divisor(self, freq_in_hz):
        if self.minor == 1:
            NOMINAL = 2e6
        else:
            NOMINAL = 4e6
        return NOMINAL / freq_in_hz;

    @min_proto_version("0.1")
    @capability('o')
    def goc_set_frequency(self, freq_in_hz):
        '''
        Sets the GOC frequency.
        '''
        if not self.get_goc_enabled():
            self.set_goc_ein(goc=1)

        # Send a 3-byte value N, where 2 MHz / N == clock speed
        self.goc_ein_set_freq_divisor(self._goc_freq_in_hz_to_divisor(freq_in_hz))

        self.goc_freq = freq_in_hz
        logger.debug("GOC frequency set to %f" % (freq_in_hz))

    @min_proto_version("0.2")
    @capability('O')
    def goc_get_onoff(self):
        '''
        Get the current ambient GOC power.
        '''
        if not self.get_goc_enabled():
            self.set_goc_ein(goc=1)

        self.min_version(0.2)
        resp = self.send_message_until_acked('O', struct.pack("B", ord('o')))
        if len(resp) != 1:
            raise self.FormatError("Wrong response length from `Oo': " + str(resp))
        onoff = struct.unpack("B", resp)[0]
        return bool(onoff)

    @min_proto_version("0.2")
    @capability('o')
    def goc_set_onoff(self, onoff):
        '''
        Turn the GOC light on or off.

        The GOC will blink as normal when goc_send is called, this simply sets
        the state of the GOC light when it's not doing anything else (e.g. so
        you can leave the light on for charging or something similar)
        '''
        if not self.get_goc_enabled():
            self.set_goc_ein(goc=1)

        self.min_version(0.2)
        msg = struct.pack("BB", ord('o'), onoff)
        self.send_message_until_acked('o', msg)

    ## I2C ##
    @min_proto_version("0.1")
    @capability('d')
    def i2c_send(self, addr, data):
        '''
        Sends an I2C message.

        Addr should be a single byte address.
        Data should be packed binary data, as returned by struct.pack

        The return value is the number of bytes actually sent *including the
        address byte*.

        Long messages may be fragmented between the ICE library and the ICE
        FPGA. On the I2C wire, this will appear as windows of time where the I2C
        clock is stretched for a period of time.  A faster baud rate between the
        PC host and the ICE FPGA will help mitigate this.
        '''

        msg = struct.pack("B", addr) + data
        return self._fragment_sender('d', msg)

    @min_proto_version("0.1")
    @capability('I')
    def i2c_get_speed(self):
        '''
        Get the clock speed of the ICE I2C driver in kHz.
        '''
        ack,msg = self.send_message('I', struct.pack("B", ord('c')))
        if ack == 0:
            if len(msg) != 1:
                raise self.FormatError
            return struct.unpack("B", msg)[0] * 2

        ret = ord(msg[0])
        msg = msg[1:]
        if ret == errno.ENODEV:
            # XXX Generalize me w.r.t. version?
            return 100
        else:
            raise self.ICE_Error("Unknown Error")

    @min_proto_version("0.1")
    @capability('i')
    def i2c_set_speed(self, speed):
        '''
        Set the clock speed of the ICE I2C driver in kHz.

        The accepted range of speeds is [2,400] kHz with steps of undefined
        increments. The actual set speed is returned.

        Raises an ICE_Error if the speed was not set.

        Note: This does *NOT* affect the clock speed of any M3 I2C drivers.
              That requires sending DMA messages to each of the M3 I2C
              controllers that you would like to change the speed of.
        '''
        if speed < 2:
            speed = 2
        elif speed > 400:
            speed = 400

        speed //= 2
        ack,msg = self.send_message('i', struct.pack("BB", ord('c'), speed))

        if ack == 0:
            return speed

        ret = ord(msg[0])
        msg = msg[1:]
        if ret == errno.EINVAL:
            raise self.ICE_Error("ICE reports: Invalid argument.")
        elif ret == errno.ENODEV:
            raise self.ICE_Error("Changing I2C speed not supported.")

    @min_proto_version("0.1")
    @capability('I')
    def i2c_get_address(self):
        '''
        Get the I2C address(es) of the ICE peripheral.
        '''
        resp = self.send_message_until_acked('I', struct.pack("B", ord('a')))
        if len(resp) != 2:
            raise self.FormatError("i2c address response should be 2 bytes")
        ones, zeros = struct.unpack("BB", resp)
        if ones == 0xff and zeros == 0xff:
            return None
        else:
            return self.masks_to_strings(ones, zeros, 8)

    @min_proto_version("0.1")
    @capability('i')
    def i2c_set_address(self, address=None):
        '''
        Set the I2C address(es) of the ICE peripheral.

        The ICE board will ACK messages sent to any address that matches the
        mask set by this function. The special character 'x' is used to signify
        don't-care bits. As example, to pretend to be the DSP layer:

           address = "1001 100x"

        Spaces are permitted and ignored. To disable this feature, set the
        address to None.

        Default Value: DISABLED.
        '''
        if address is None:
            ones, zeros = (0xff, 0xff)
        else:
            if len(address) != 8:
                raise self.FormatError("Address must be exactly 8 bits")
            ones, zeros = self.string_to_masks(address)
        self.send_message_until_acked('i', struct.pack("BBB", ord('a'), ones, zeros))

    ## MBus ##
    @min_proto_version("0.2")
    @capability('b')
    def mbus_send(self, addr, data):
        '''
        Sends an MBus message.

        Addr may be a short address or long address. In either case, it should
        be packed binary data (e.g. struct.pack or binascii.unhexlify('a5'))

        The return value is the number of bytes actually sent *including four
        bytes for the address, regardless of whether a short or long address was
        actually sent*.

        Long messages may be fragmented between the ICE library and the ICE
        FPGA. On the wire, this should not be noticeable as the PC<-->ICE bridge
        (3 MBaud) is much faster than the MBus. If this is an issue, you must
        keep the transaction size below the ICE fragmentation limit (less than
        255 bytes for combined address + data).
        '''
        self.min_version(0.2)
        if type(addr) != bytes:
            addr = bytes(addr, 'utf-8')
        if len(addr) > 4:
            raise self.FormatError("Address too long: " + str(addr) +\
                                    ' len:' + str(len(addr)))
        while len(addr) < 4:
            zero = bytes(1)
            addr = zero + addr
        msg = addr + data
        return self._fragment_sender('b', msg)

    @min_proto_version("0.3")
    @capability('m')
    def mbus_set_internal_reset(self, assert_reset):
        '''
        Control signal that holds ICE internal MBus in reset.

        While in reset, the COUT and DOUT signals are held high. This is useful
        for bootstrapping when multiple ICE boards are in a loop.
        '''
        self.min_version(0.3)
        self.send_message_until_acked('m', struct.pack("B"*(1+1),
            ord('r'),
            bool(assert_reset),
            ))

    @min_proto_version("0.2")
    @capability('m')
    def mbus_set_full_prefix(self, prefix=None):
        '''
        Set the full prefix(es) of the ICE peripheral.

        The ICE board will ACK messages sent to any address that matches the
        mask set by this function. The special character 'x' is used to signify
        don't-care bits.

        Spaces are permitted and ignored. To disable this feature, set the
        address to None.

        Default Value: DISABLED.
        '''
        self.min_version(0.2)
        if prefix is None:
            ones, zeros = (0xfffff, 0xfffff)
        else:
            if len(prefix) != 20:
                raise self.FormatError("Prefix must be exactly 20 bits")
            ones, zeros = self.string_to_masks(prefix)
        ones <<= 4
        zeros <<= 4
        self.send_message_until_acked('m', struct.pack("B"*(1+6),
            ord('l'),
            (ones >> 16) & 0xff,
            (ones >> 8) & 0xff,
            ones & 0xff,
            (zeros >> 16) & 0xff,
            (zeros >> 8) & 0xff,
            zeros & 0xff,
            ))

    @min_proto_version("0.2")
    @capability('M')
    def mbus_get_full_prefix(self):
        '''
        Get the full prefix(es) set for ICE.
        '''
        self.min_version(0.2)
        resp = self.send_message_until_acked('M', struct.pack("B", ord('l')))
        if len(resp) != 6:
            raise self.FormatError("Full prefix response should be 6 bytes")
        o_hig, o_mid, o_low, z_hig, z_mid, z_low = struct.unpack("BBBBBB", resp)
        ones = o_low | o_mid << 8 | o_hig << 16
        zeros = z_low | z_mid << 8 | z_hig << 16
        ones >>= 4
        zeros >>= 4
        if ones == 0xfffff and zeros == 0xfffff:
            return None
        else:
            return self.masks_to_strings(ones, zeros, 20)

    @min_proto_version("0.2")
    @capability('m')
    def mbus_set_short_prefix(self, prefix=None):
        '''
        Set the short prefix(es) of the ICE peripheral.

        Default Value: DISABLED.
        '''
        self.min_version(0.2)
        if prefix is None:
            ones, zeros = (0xf, 0xf)
        else:
            if prefix.startswith('0x'):
                try: # try converting from hex
                    prefix = "{0:b}".format( int(prefix,16) )
                except: 
                    raise self.FormatError("Malformed Prefix")
                logger.debug("Prefix parsed as: " + str(prefix))

            if len(prefix) != 4:
                raise self.FormatError("Prefix must be exactly 4 bits")

            ones, zeros = self.string_to_masks(prefix)

        self.send_message_until_acked('m', struct.pack("B"*(1+1),
            ord('s'),
            ones,
            ))

    @min_proto_version("0.2")
    @capability('M')
    def mbus_get_short_prefix(self):
        '''
        Get the short prefix(es) set for ICE.
        '''
        self.min_version(0.2)
        resp = self.send_message_until_acked('M', struct.pack("B", ord('s')))
        if len(resp) != 2:
            raise self.FormatError("Full prefix response should be 2 bytes")
        ones, zeros = struct.unpack("BB", resp)
        ones >>= 4
        zeros >>= 4
        if ones == 0xf and zeros == 0xf:
            return None
        else:
            return self.masks_to_strings(ones, zeros, 4)

    @min_proto_version("0.3")
    @capability('m')
    def mbus_set_snoop(self, enable, filter_prefix=None):
        '''
        Enable snooping of all traffic. The optional filter runs in software to limit reported messages.

        Default Value: DISABLED.
        '''
        self.min_version(0.3)
        enable = bool(enable)
        if filter_prefix is not None:
            raise NotImplementedError
        self.send_message_until_acked('m', struct.pack("B"*(1+1),
            ord('S'),
            enable,
            ))

    @min_proto_version("0.3")
    @capability('M')
    def mbus_get_snoop(self, return_filter=False):
        '''
        Return whether snooping is enabled.
        '''
        self.min_version(0.3)
        resp = self.send_message_until_acked('M', struct.pack("B", ord('S')))
        if len(resp) != 1:
            raise self.FormatError("Snoop enabled response should be 1 byte")
        enabled = bool(struct.unpack("B", resp))

        if return_filter:
            raise NotImplementedError
        return enabled

    @min_proto_version("0.2")
    @capability('m')
    def mbus_set_broadcast_channel_mask(self, mask=None):
        '''
        Set the broadcast mask for ICE board.

        The ICE board will report and ACK any messages sent to broadcast
        channels that match the mask set by this function. The special character 'x' is
        used to signify don't-care bits.

        Spaces are permitted and ignored. To disable this feature, set the
        address to None.

        Default Value: DISABLED.
        '''
        self.min_version(0.2)
        if mask is None:
            ones, zeros = (0xf, 0xf)
        else:
            if len(mask) != 4:
                raise self.FormatError("Prefix must be exactly 4 bits")
            ones, zeros = self.string_to_masks(mask)
        self.send_message_until_acked('m', struct.pack("B"*(1+2),
            ord('b'),
            ones,
            zeros,
            ))

    @min_proto_version("0.2")
    @capability('M')
    def mbus_get_broadcast_channel_mask(self):
        '''
        Get the broadcast mask for ICE.
        '''
        self.min_version(0.2)
        resp = self.send_message_until_acked('M', struct.pack("B", ord('b')))
        if len(resp) != 2:
            raise self.FormatError("Broadcast mask response should be 2 bytes")
        ones, zeros = struct.unpack("BB", resp)
        if ones == 0xf and zeros == 0xf:
            return None
        else:
            return self.masks_to_strings(ones, zeros, 4)

    @min_proto_version("0.2")
    @capability('m')
    def mbus_set_broadcast_channel_snoop_mask(self, mask=None):
        '''
        Set the broadcast snoop mask for ICE board.

        The ICE board will report, but not ACK, any messages sent to broadcast
        channels that match the mask set by this function. The special character 'x' is
        used to signify don't-care bits.

        Spaces are permitted and ignored. To disable this feature, set the
        address to None.

        Default Value: DISABLED.
        '''
        self.min_version(0.2)
        if mask is None:
            ones, zeros = (0xf, 0xf)
        else:
            if len(mask) != 4:
                raise self.FormatError("Prefix must be exactly 4 bits")
            ones, zeros = self.string_to_masks(mask)
        self.send_message_until_acked('m', struct.pack("B"*(1+2),
            ord('B'),
            ones,
            zeros,
            ))

    @min_proto_version("0.2")
    @capability('M')
    def mbus_get_broadcast_channel_snoop_mask(self):
        '''
        Get the broadcast snoop mask for ICE.
        '''
        self.min_version(0.2)
        resp = self.send_message_until_acked('M', struct.pack("B", ord('B')))
        if len(resp) != 2:
            raise self.FormatError("Broadcast mask response should be 2 bytes")
        ones, zeros = struct.unpack("BB", resp)
        if ones == 0xf and zeros == 0xf:
            return None
        else:
            return self.masks_to_strings(ones, zeros, 4)

    @min_proto_version("0.2")
    @capability('M')
    def mbus_get_master_onoff(self):
        '''
        Get whether ICE is acting as MBus master node.
        '''
        self.min_version(0.2)
        resp = self.send_message_until_acked('M', struct.pack("B", ord('m')))
        if len(resp) != 1:
            raise self.FormatError("Wrong response length from `Mm': " + str(resp))
        onoff = struct.unpack("B", resp)[0]
        return bool(onoff)

    @min_proto_version("0.2")
    @capability('m')
    def mbus_set_master_onoff(self, onoff):
        '''
        Set whether ICE acts as MBus master node.

        DEFAULT: OFF
        '''
        self.min_version(0.2)
        if isinstance(onoff, str):
            onoff = onoff.lower()
            onoff = True if onoff in ['on'] else False
        elif isinstance(onoff, bool):
            pass
        else: raise Exception("Bad arg for " + __name__ )

        msg = struct.pack("BB", ord('m'), onoff)
        self.send_message_until_acked('m', msg)

    @min_proto_version("0.2")
    @capability('M')
    def mbus_get_clock(self):
        '''
        Get ICE MBus clock speed. Only meaningful if ICE is MBus master.
        '''
        self.min_version(0.2)
        raise NotImplementedError
        #resp = self.send_message_until_acked('M', struct.pack("B", ord('c')))
        #if len(resp) != 1:
        #    raise self.FormatError("Wrong response length from `Mc': " + str(resp))
        #onoff = struct.unpack("B", resp)[0]
        #return bool(onoff)
        #return resp

    @min_proto_version("0.2")
    @capability('m')
    def mbus_set_clock(self, clock_speed):
        '''
        Set ICE MBus clock speed. Only meaningful if ICE is MBus master.

        DEFAULT: XXX
        '''
        self.min_version(0.2)
        #msg = struct.pack("BB", ord('c'), onoff)
        #self.send_message_until_acked('m', msg)
        raise NotImplementedError

    @min_proto_version("0.2")
    @capability('M')
    def mbus_get_should_interrupt(self):
        '''
        Get ICE MBus should interrupt setting.

        TODO: Fix interface (enums?)
        '''
        self.min_version(0.2)
        resp = self.send_message_until_acked('M', struct.pack("B", ord('i')))
        resp = ord(resp)
        #if len(resp) != 1:
        #    raise self.FormatError("Wrong response length from `Mc': " + str(resp))
        #onoff = struct.unpack("B", resp)[0]
        #return bool(onoff)
        return resp

    @min_proto_version("0.2")
    @capability('m')
    def mbus_set_should_interrupt(self, should_interrupt):
        '''
        Set ICE MBus should interrupt setting.

        DEFAULT: Off
        '''
        self.min_version(0.2)
        msg = struct.pack("BB", ord('i'), should_interrupt)
        self.send_message_until_acked('m', msg)

    @min_proto_version("0.2")
    @capability('M')
    def mbus_get_use_priority(self):
        '''
        Get ICE MBus use priority setting.

        TODO: Fix interface (enums?)
        '''
        self.min_version(0.2)
        resp = self.send_message_until_acked('M', struct.pack("B", ord('p')))
        resp = ord(resp)
        #if len(resp) != 1:
        #    raise self.FormatError("Wrong response length from `Mc': " + str(resp))
        #onoff = struct.unpack("B", resp)[0]
        #return bool(onoff)
        return resp

    @min_proto_version("0.2")
    @capability('m')
    def mbus_set_use_priority(self, use_priority):
        '''
        Set ICE MBus use priority setting.

        DEFAULT: Off
        '''
        self.min_version(0.2)
        msg = struct.pack("BB", ord('p'), use_priority)
        self.send_message_until_acked('m', msg)

    ## EIN DEBUG ##
    EIN_DEFAULT_DIVISOR = 0xFA0

    @min_proto_version("0.2")
    @capability('f')
    def ein_send(self, msg):
        '''
        Sends a message via the EIN Debug port.

        Takes a raw byte stream (e.g. binascii.unhexlify("aa")).
        Returns the number of bytes actually sent.

        Long messages may be fragmented between the ICE library and the ICE
        FPGA. These fragments will be combined on the ICE board. There should be
        no interruption in message transmission.
        '''
        if not self.get_ein_enabled():
            self.set_goc_ein(ein=1)

        self.min_version(0.2)
        ret = self._fragment_sender('f', msg)
        return ret

    ## GPIO ##
    # XXX TODO XXX: parameter based method version selection
    GPIO_INPUT = 0
    GPIO_OUTPUT = 1
    GPIO_TRISTATE = 2

    def gpio_get_level(self, gpio_idx):
        '''
        Query whether a gpio is high or low. (high=True)
        '''
        if self.minor == 1:
            return self.gpio_get_level_0_1(gpio_idx)
        else:
            return self.gpio_get_level_0_2(gpio_idx)

    def gpio_get_direction(self, gpio_idx):
        '''
        Query gpio pin setup.

        Returns one of:
            ICE.GPIO_INPUT
            ICE.GPIO_OUTPUT
            ICE.GPIO_TRISTATE
        '''
        if self.minor == 1:
            return self.gpio_get_direction_0_1(gpio_idx)
        else:
            return self.gpio_get_direction_0_2(gpio_idx)

    def gpio_set_level(self, gpio_idx, level):
        '''
        Set gpio level. (high=True)
        '''
        if self.minor == 1:
            return self.gpio_set_level_0_1(gpio_idx, level)
        else:
            return self.gpio_set_level_0_2(gpio_idx, level)

    def gpio_set_direction(self, gpio_idx, direction):
        '''
        Setup a GPIO pin.
        '''
        if direction not in (ICE.GPIO_INPUT, ICE.GPIO_OUTPUT, ICE.GPIO_TRISTATE):
            raise self.ParameterError("Unknown direction: " + str(direction))
        if self.minor == 1:
            return self.gpio_set_direction_0_1(gpio_idx, direction)
        else:
            return self.gpio_set_direction_0_2(gpio_idx, direction)

    @min_proto_version("0.1")
    @max_proto_version("0.1")
    @capability('G')
    def gpio_get_level_0_1(self, gpio_idx):
        resp = self.send_message_until_acked('G',
                struct.pack('BB', ord('l'), gpio_idx))
        if len(resp) != 1:
            raise self.FormatError("Too long of a response from `Gl#':" + str(resp))

        return bool(struct.unpack("B", resp)[0])

    @min_proto_version("0.1")
    @max_proto_version("0.1")
    @capability('G')
    def gpio_get_direction_0_1(self, gpio_idx):
        resp = self.send_message_until_acked('G',
                struct.pack('BB', ord('d'), gpio_idx))
        if len(resp) != 1:
            raise self.FormatError("Too long of a response from `Gd#':" + str(resp))

        direction = struct.unpack("B", resp)[0]
        if direction not in (ICE.GPIO_INPUT, ICE.GPIO_OUTPUT, ICE.GPIO_TRISTATE):
            raise self.FormatError("Unknown direction: " + str(direction))

        return direction

    @min_proto_version("0.1")
    @max_proto_version("0.1")
    @capability('g')
    def gpio_set_level_0_1(self, gpio_idx, level):
        self.send_message_until_acked('g',
                struct.pack('BBB', ord('l'), gpio_idx, level))

    @min_proto_version("0.1")
    @max_proto_version("0.1")
    @capability('g')
    def gpio_set_direction_0_1(self, gpio_idx, direction):
        self.send_message_until_acked('g',
                struct.pack('BBB', ord('d'), gpio_idx, direction))

    def _gpio_get_level_0_2(self):
        resp = self.send_message_until_acked('G', struct.pack('B', ord('l')))
        if len(resp) != 3:
            raise self.FormatError("Bad response from `Gl':" + str(resp))
        high,mid,low = map(ord, resp)
        return low | (mid << 8) | (high << 16)

    @min_proto_version("0.2")
    @capability('G')
    def gpio_get_level_0_2(self, gpio_idx):
        if gpio_idx >= 24:
            raise self.ParameterError("Request for illegal gpio idx")
        return (self._gpio_get_level_0_2() >> gpio_idx) & 0x1

    def _gpio_get_direction_0_2(self):
        resp = self.send_message_until_acked('G', struct.pack('B', ord('d')))
        if len(resp) != 3:
            raise self.FormatError("Bad response from `Gd#':" + str(resp))
        high,mid,low = map(ord, resp)
        return low | (mid << 8) | (high << 16)

    @min_proto_version("0.2")
    @capability('G')
    def gpio_get_direction_0_2(self, gpio_idx):
        if gpio_idx >= 24:
            raise self.ParameterError("Request for illegal gpio idx")

        if ((self._gpio_get_direction_0_2() >> gpio_idx) & 0x1) == 0:
            return ICE.GPIO_INPUT
        else:
            return ICE.GPIO_OUTPUT

    @min_proto_version("0.2")
    @capability('g')
    def gpio_set_level_0_2(self, gpio_idx, level):
        mask = self._gpio_get_level_0_2()
        if level:
            mask |= (1 << gpio_idx)
        else:
            mask &= ~(1 << gpio_idx)
        self.send_message_until_acked('g',
                struct.pack('BBBB', ord('l'),
                    (mask >> 16) & 0xff,
                    (mask >> 8) & 0xff,
                    mask & 0xff))

    @min_proto_version("0.2")
    @capability('g')
    def gpio_set_direction_0_2(self, gpio_idx, direction):
        mask = self._gpio_get_direction_0_2()
        if direction == ICE.GPIO_OUTPUT:
            mask |= (1 << gpio_idx)
        elif direction in (ICE.GPIO_INPUT, ICE.GPIO_TRISTATE):
            mask &= ~(1 << gpio_idx)
        else:
            raise self.ParameterError("Illegal GPIO direction")
        self.send_message_until_acked('g',
                struct.pack('BBBB', ord('d'),
                    (mask >> 16) & 0xff,
                    (mask >> 8) & 0xff,
                    mask & 0xff))

    @min_proto_version("0.2")
    @capability('G')
    def gpio_get_interrupt_enable_mask(self):
        resp = self.send_message_until_acked('G', struct.pack('B', ord('i')))
        if len(resp) != 3:
            raise self.FormatError("Bad response from `Gi':" + str(resp))
        high,mid,low = map(ord, resp)
        return low | (mid << 8) | (high << 16)

    @min_proto_version("0.2")
    @capability('g')
    def gpio_set_interrupt_enable_mask(self, mask):
        self.send_message_until_acked('g',
                struct.pack('BBBB', ord('i'),
                    (mask >> 16) & 0xff,
                    (mask >> 8) & 0xff,
                    mask & 0xff))

    ## POWER ##
    POWER_0P6 = 0
    POWER_1P2 = 1
    POWER_VBATT = 2
    POWER_GOC = 3

    POWER_0P6_DEFAULT = 0.675
    POWER_1P2_DEFAULT = 1.2
    POWER_VBATT_DEFAULT = 3.8

    @min_proto_version("0.1")
    @capability('P')
    def power_get_voltage(self, rail):
        '''
        Query the current voltage setting of a power rail.

        The `rail' argument must be one of:
            ICE.POWER_0P6
            ICE.POWER_1P2
            ICE.POWER_VBATT
        '''
        if rail not in (ICE.POWER_0P6, ICE.POWER_1P2, ICE.POWER_VBATT):
            raise self.ParameterError("Invalid rail: " + str(rail))

        logger.warn("ICE Firmware <= 0.3 cannot query voltage. Returning cached value.")
        try:
            raw = getattr(self, 'power_{}'.format(rail))
        except AttributeError:
            logger.warn("No cached value, returning default")
            raw = (1 - 0.537) / 0.0185

        #resp = self.send_message_until_acked('P', struct.pack("BB", ord('v'), rail))
        #if len(resp) != 2:
        #    raise self.FormatError("Wrong response length from `Pv#':" + str(resp))
        #rail, raw = struct.unpack("BB", resp)

        # Vout = (0.537 + 0.0185 * v_set) * Vdefault
        default_voltage = (ICE.POWER_0P6_DEFAULT, ICE.POWER_1P2_DEFAULT,
                ICE.POWER_VBATT_DEFAULT)[rail]
        vout = (0.537 + 0.0185 * raw) * default_voltage
        return vout

    # didn't actually work until v0.5
    @min_proto_version("0.5")
    @capability('P')
    def power_get_onoff(self, rail):
        '''
        Query the current on/off setting of a power rail.

        Returns a boolean, on=True.
        '''
        if rail not in (ICE.POWER_0P6, ICE.POWER_1P2, ICE.POWER_VBATT, ICE.POWER_GOC):
            raise self.ParameterError("Invalid rail: " + str(rail))

        resp = self.send_message_until_acked('P', struct.pack("BB", ord('o'), rail))
        if len(resp) != 1:
            raise self.FormatError("Too long of a response from `Po#':" + str(resp))
        onoff = struct.unpack("B", resp)[0]
        return bool(onoff)

    @min_proto_version("0.1")
    @capability('p')
    def power_set_voltage(self, rail, output_voltage):
        '''
        Set the voltage setting of a power rail. Units are V.
        '''
        if rail not in (ICE.POWER_0P6, ICE.POWER_1P2, ICE.POWER_VBATT):
            raise self.ParameterError("Invalid rail: " + str(rail))

        # Vout = (0.537 + 0.0185 * v_set) * Vdefault
        output_voltage = float(output_voltage)
        default_voltage = (ICE.POWER_0P6_DEFAULT, ICE.POWER_1P2_DEFAULT,
                ICE.POWER_VBATT_DEFAULT)[rail]
        vset = ((output_voltage / default_voltage) - 0.537) / 0.0185
        vset = int(vset)
        if (vset < 0) or (vset > 255):
            raise self.ParameterError("Voltage exceeds range. vset: " + str(vset))

        self.send_message_until_acked('p', struct.pack("BBB", ord('v'), rail, vset))
        setattr(self, 'power_{}'.format(rail), vset)

    @min_proto_version("0.1")
    @capability('p')
    def power_set_onoff(self, rail, onoff):
        '''
        Turn a power rail on or off (on=True).
        '''
        if rail not in (ICE.POWER_0P6, ICE.POWER_1P2, ICE.POWER_VBATT, ICE.POWER_GOC):
            raise self.ParameterError("Invalid rail: " + str(rail))

        self.send_message_until_acked('p', struct.pack("BBB", ord('o'), rail, onoff))

if __name__ == '__main__':
    logger.setLevel(level=logging.DEBUG)

