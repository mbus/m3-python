#!/usr/bin/env python

import time

from m3_common import m3_common
from m3_common import mbus_snooper
from m3_common import ein_programmer
from m3_common import goc_programmer

from m3_logging import get_logger
logger = get_logger(__name__)

class m3_ice(m3_common):
    TITLE = "M3 ICE Interface"
    DESCRIPTION = "Tool to control the ICE board and attached M3 chips."
    MSG_TYPE = 'b+'

    def add_parse_args(self):
        super(m3_ice, self).add_parse_args()

        self.subparsers = self.parser.add_subparsers(
                title='Commands',
                description='Actions supported by the ICE board',
                )

        #aliases in Py3k only :(
        #self.parser_softreset = self.subparsers.add_parser('softreset',
        #        aliases=['reset'],
        #        help='Cycle 0.6V rail to reset M3 chips')
        self.parser_softreset = self.subparsers.add_parser('reset',
                help='Cycle 0.6V rail to reset M3 chips')
        self.parser_softreset.set_defaults(func=self.cmd_softreset)

        self.parser_hardreset = self.subparsers.add_parser('hardreset',
                help='Cycle all power rails to cold-boot M3 chips')
        self.parser_hardreset.set_defaults(func=self.cmd_hardreset)

        self.parser_power = self.subparsers.add_parser('power',
                help='Control power rails sent to connected M3 chips')
        self.parser_power.add_argument('STATE',
                choices=['on', 'off'],
                help='Power M3 chips on or off')
        self.parser_power.set_defaults(func=self.cmd_power)

        self.parser_snoop = self.subparsers.add_parser('snoop',
                help='Passively monitor MBus messages')
        mbus_snooper.add_parse_args(self.parser_snoop)
        self.parser_snoop.set_defaults(func=self.cmd_snoop)

        self.parser_ein = self.subparsers.add_parser('ein',
                help='Command the chip via the EIN protocol')
        ein_programmer.add_parse_args(self.parser_ein)
        self.parser_ein.set_defaults(func=self.cmd_ein)

        self.parser_goc = self.subparsers.add_parser('goc',
                help='Send commands via the GOC protocol (blinking light)')
        self.goc_programmer = goc_programmer(self, self.parser_goc)
        self.parser_goc.set_defaults(func=self.cmd_goc)

    def cmd_softreset(self):
        self.ice.power_set_onoff(self.ice.POWER_0P6, False)
        time.sleep(.5)
        self.ice.power_set_onoff(self.ice.POWER_0P6, True)
        logger.info("Soft reset complete.")

    def cmd_hardreset(self):
        self._cmd_power_off()
        time.sleep(2)

        self._cmd_power_on()
        time.sleep(4)

        self.cmd_softreset()

    def _cmd_power_off(self):
        self.ice.power_set_onoff(self.ice.POWER_0P6, False)
        self.ice.power_set_onoff(self.ice.POWER_1P2, False)
        self.ice.power_set_onoff(self.ice.POWER_VBATT, False)
        logger.info("Power off.")

    def _cmd_power_on(self):
        self.ice.power_set_onoff(self.ice.POWER_VBATT, True)
        self.ice.power_set_onoff(self.ice.POWER_1P2, True)
        self.ice.power_set_onoff(self.ice.POWER_0P6, True)
        logger.info("Power on.")

    def cmd_power(self):
        getattr(self, '_cmd_power_{}'.format(self.args.STATE))()

    def cmd_snoop(self):
        snooper = mbus_snooper(self.args, self.ice)
        self.hang_for_messages()

    def cmd_ein(self):
        ein = ein_programmer(self)
        ein.cmd()

    def cmd_goc(self):
        #goc_programmer(self).cmd()
        pass


def cmd():
    m = m3_ice()
    m.args.func()

if __name__ == '__main__':
    cmd()

