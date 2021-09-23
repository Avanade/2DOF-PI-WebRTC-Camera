# Copyright (c) 2021 Avanade
# Author: Thor Schueler
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#
# pylint: disable=C0103
# pylint: disable=R0913
"""
    This library implement the interface to the Arducam PTZ controller. The controller
    has 2 regular servo channels (pan and tilt, assumed to be on channel 0 and 1 respectively) and
    two micro stepper motors on channels 2 and 3 respectively. We treat the steppers as 
    continous rotation motors with upper limits. 
"""
import logging
import time
import math
import json
from typing import Dict
from jsonschema import validate
from .controller import Controller
from .servo import Servo
from .servo_attributes import ServoAttributes
from .schemas import controller_schema as schema

CHIP_I2C_ADDR = 0x0C
BUSY_REG_ADDR = 0x04

OPT_BASE    = 0x1000
OPT_FOCUS   = OPT_BASE | 0x01
OPT_ZOOM    = OPT_BASE | 0x02
OPT_MOTOR_X = OPT_BASE | 0x03
OPT_MOTOR_Y = OPT_BASE | 0x04
OPT_IRCUT   = OPT_BASE | 0x05

logger = logging.getLogger('controller')
opts = {
    OPT_FOCUS : {
        "REG_ADDR" : 0x01,
        "MAX_VALUE": 18000,
        "RESET_ADDR": 0x01 + 0x0A,
    },
    OPT_ZOOM  : {
        "REG_ADDR" : 0x00,
        "MAX_VALUE": 18000,
        "RESET_ADDR": 0x00 + 0x0A,
    },
    OPT_MOTOR_X : {
        "REG_ADDR" : 0x05,
        "MAX_VALUE": 180,
        "RESET_ADDR": None,
    },
    OPT_MOTOR_Y : {
        "REG_ADDR" : 0x06,
        "MAX_VALUE": 180,
        "RESET_ADDR": None,
    },
    OPT_IRCUT : {
        "REG_ADDR" : 0x0C, 
        "MAX_VALUE": 0x01,   #0x0001 open, 0x0000 close
        "RESET_ADDR": None,
    }
}

class ArduCamPTZ(Controller):
    """ArduCam PTZ controller."""
 
    def __init__(self, address: int = CHIP_I2C_ADDR, i2c = None, 
                 frequency: int = 26500000, resolution: int = 4096,
                 servo_frequency: int = 50, **kwargs):
        """__init__

        Initialize the ArduCamPTZ controller.

        :param address: The hardware address of the board. Generally 0x12 unless there is more
                        than one board.
        :type address: integer

        :param i2c: I2C driver object. Generally should be None to self obtain.
        :type i2c: smbus.SMBus

        :param frequency: The boards oscillating frequency. Will be around 25MHz, but will
                          slightly vary by board. This is not used for ArduCamPTZ.
        :type frequency: integer

        :param resolution: The pulse interval resolution. It is 12 bit. You should not have
                           to change that. This is not used for ArduCamPTZ.
        :type resolution: integer

        :param servo_frequency: The pulse frequency for the attached servos. All servos on the
                                board share the same frequency. This is not used for ArduCamPTZ.
        :type servo_frequency: integer

        :param kwargs: additional arguments
        :type kwards: point to object array

        """
        super().__init__(address, i2c, frequency, resolution, servo_frequency, **kwargs)
        self.__i2c = ArduCamPTZ.__ensureI2C(i2c)
        logger.info("Registered controller on address %d" % address)

    @classmethod
    def __ensureI2C(cls, i2c=None):
        """Ensures I2C device interface"""
        if i2c is None:
            logger.info('Initializing SMBUS(I2C).')
            import smbus
            i2c = smbus.SMBus(1)
        return i2c

    @classmethod
    def from_dict(cls, data:Dict[str, object]) -> object:
        """from_dict
        Generates PCA9685 from dictionary
        :param data: The dictionary containing the servo data. Must adhere to Controller.ControllerSchema
        :type data: dictionary
        """
        instance = cls(
            data['address'],
            None,
            data['frequency'],
            data['resolution'],
            data['servo_frequency']
        )
        if 'logging_level' in data: logger.setLevel(data['logging_level'])
        return instance

    def add_servo(self, channel: int, attributes: ServoAttributes = None, move_to_neutral: bool = True):
        """add_servo
        Adds a servo definition for a given channel.

        :param channel: The channel on which the servo is operating.
        :type channel: integer

        :param attributes: The servo attribute (min/max/neutral pulses and angles).
        :type attributes: ServoAttributes

        :param move_to_neutral: Move the servo to the neutral position if True
        :type move_to_neutral: bool

        """
        if channel not in (0x0, 0x1, 0x5, 0x6, 0x0C):
            raise ValueError('Channel must be between 0, 1, 5, 6 or 12')

        if channel in self._servos:
            raise KeyError('There is already a servo on this channel: %d', channel)

        self._servos[channel] = Servo(self, channel, attributes, move_to_neutral)
        self._servos[channel].set_angle(float(self.__read(channel)))

    def set_servo_pulse(self, channel: int, pulse: float):
        """set_servo_pulse
        Sets the servo on channel to a certain pulse width.

        :param channel: The channel for which to obtain the servo state. 0 (Zoom), 1 (Focus), 5 (Pan), 6 (Tilt) or 12 (IrCut).
        :type channel: integer

        :param pulse: The pulse length to set.
        :type pulse: float

        """
        if channel not in (0x0, 0x1, 0x5, 0x6, 0x0C):
            raise ValueError('Channel must be between 0, 1, 5, 6 or 12')

        if channel not in self._servos:
            raise KeyError('There is no servo registered on channel %d' % channel)
        
        servo = self._servos[channel]
        servo.set_pulse(pulse)

    def set_servo_angle(self, channel: int, angle: float):
        """set_servo_angle
        Sets the servo on channel to a certain angle.

        :param channel: The channel for which to obtain the servo state. 0 (Zoom), 1 (Focus), 5 (Pan), 6 (Tilt) or 12 (IrCut).
        :type channel: integer

        :param angle: The angle to set. The finest resolution is about 0.5 degrees.
        :type pulse: angle

        """
        if channel not in (0x0, 0x1, 0x5, 0x6, 0x0C):
            raise ValueError('Channel must be between 0, 1, 5, 6 or 12')

        if channel not in self._servos:
            raise KeyError('There is no servo registered on channel %d' % channel)
        
        servo = self._servos[channel]
        servo.set_angle(angle)

    def set_pwm_freq(self, servo_frequency: int):
        """set_pwm_freq
        Set the PWM frequency to the provided value in hertz.

        :param servo_frequency: The frequency of the servo pulse.
        :type servo_frequency: integer

        """
        logger.error(f"{datetime.datetime.now()}: Settings pwm frequeny not supported for ArduCam PTZ controllers")
        raise Exception("Settings pwm frequeny not supported for ArduCam PTZ controllers")

    def set_off(self, channel: int, tf: bool = True):
        """set_off
        Toggles channel off state. Note that turning the channel totally of will not retain the servo angle
        against manual manipulation

        :param channel: The channel on which to operate.
        :type channel: int
        
        :param tf: Set to True to turn the channel off, False to turn the channel back to PWM
        :type tf: bool
        
        """
        pass

    def set_pwm(self, channel: int, on_ticks: int, off_ticks: int):
        """set_pwm
        Sets a single PWM channel pulse.

        :param channel: The channel for which to obtain the servo state. 0 (Zoom), 1 (Focus), 5 (Pan), 6 (Tilt) or 12 (IrCut).
        :type channel: integer

        :param on_ticks: Number of ticks into a period at which to switch the pulse on.
        :type pulse: integer

        :param off_ticks: Number of ticks into a period at which to switch the pulse off.
        :type pulse: integer

        """
        if channel not in (0x0, 0x1, 0x5, 0x6, 0x0C):
            raise ValueError('Channel must be between 0, 1, 5, 6 or 12')
        if on_ticks < 0:
            raise ValueError('Value for on_ticks must be greater or equaly to zero')
        if on_ticks > off_ticks:
            raise ValueError('Value for on_ticks must be less than or equal to value for off_ticks')
        logger.error(f"set val on channel {channel}")
        if channel in (0x6, 0x5):
            servo = self._servos[channel]
            angle = int(servo.angle)
            self.__write(channel, angle)
            self.__waitingForFree()

        if channel in (0x0, 0x1):
            servo = self._servos[channel]
            pulse = int(servo.pulse)
            logger.error(f"{off_ticks}-{on_ticks} -> {pulse}")
            self.__write(channel, pulse)
            self.__waitingForFree()

        if channel == 0x0C:
            servo = self._servos[channel]
            pulse = int(servo.pulse)
            logger.error(f"{off_ticks}-{on_ticks} -> {pulse}")
            self.__write(channel, 0x0 if pulse==0 else 0x1)
            self.__waitingForFree()

    def set_all_pwm(self, on_ticks: int, off_ticks: int):
        """set_pwm
        Sets all PWM channel pulse.

        :param on_ticks: Number of ticks into a period at which to switch the pulse on.
        :type pulse: integer

        :param off_ticks: Number of ticks into a period at which to switch the pulse off.
        :type pulse: integer

        """
        logger.error(f"{datetime.datetime.now()}: set_all_pwm() not supported for ArduCam PTZ controllers")
        raise Exception("set_all_pwm() not supported for ArduCam PTZ controllers")

    def software_reset(self, i2c=None, **kwargs):
        """Sends a software reset (SWRST) command to all servo drivers on the bus."""
        bus = ArduCamPTZ.__ensureI2C(i2c)
        for key in opts.keys():
            info = opts[key]
            if info == None or info["RESET_ADDR"] == None: continue
            self.__waitingForFree()
            self.__write(info["RESET_ADDR"], 0x0000)
        self.__waitingForFree()
        logger.info('Servo controllers have been reset.')

    def __isBusy(self) -> bool:
        """Determines whether the controller is currently busy executing operations"""
        return self.__read(BUSY_REG_ADDR) != 0

    def __waitingForFree(self, timeout:int=5, period:float=0.01):
        """
        Waits until all pending operations currently queued with the controller are complete.
        """
        count = 0
        begin = time.time()
        while self.__isBusy() and count < (timeout / period):
            count += 1
            time.sleep(period)
        if count >= (timeout / period):
            logger.warning(f"{datetime.datetime.now()}: Timeout waiting for controller to complete work")

    def __read(self, reg_addr:int) -> int:
        """Reads the controller's registry value on a specified address"""
        value = self.__i2c.read_word_data(self._address,reg_addr)
        value = ((value & 0x00FF)<< 8) | ((value & 0xFF00) >> 8)
        return value

    def __write(self,reg_addr: int, value:int):
        """
        Sets a registry value in the controller

        :param reg_addr:    Address of the register to write
        :type reg_addr:     int

        :param value:       Value to write
        :type value:        int
        """
        if value < 0: value = 0
        value = ((value & 0x00FF)<< 8) | ((value & 0xFF00) >> 8)
        return self.__i2c.write_word_data(self._address,reg_addr,value)
