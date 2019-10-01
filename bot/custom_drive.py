from math import cos, atan2, pi, radians

from rlbot.agents.base_agent import SimpleControllerState
from rlutilities.linear_algebra import vec3, dot
from rlutilities.mechanics import Drive as RLUDrive

from util import sign, cap

class CustomDrive:
    
    def __init__(self, car):
        self.car = car
        self.target = (0, 0, 0)
        self.speed = 2300
        self.controls = SimpleControllerState()
        self.finished = False
        self.rlu_drive = RLUDrive(self.car)
        self.update_rlu_drive()


    def step(self, dt: float):
        self.update_rlu_drive()
        self.rlu_drive.step(dt)
        self.finished = self.rlu_drive.finished

        car_to_target = (self.target - self.car.location)
        local_target = dot(car_to_target, self.car.rotation)
        angle = atan2(local_target[1], local_target[0])

        self.controls = self.rlu_drive.controls
        reverse = (cos(angle) < 0)
        if reverse:
            self.controls.throttle = (-self.controls.throttle - 1) / 2
            #self.controls.throttle = -1
            angle = -self.invert_angle(angle)
            self.controls.steer = cap(angle * 3, -1, 1)
            self.controls.boost = False
        self.controls.handbrake = (abs(angle) > radians(75))


    def update_rlu_drive(self):
        self.target = vec3(self.target[0], self.target[1], self.target[2])
        self.rlu_drive.target = self.target
        self.rlu_drive.speed = self.speed
        #self.rlu_drive.car = self.car


    def invert_angle(self, angle: float):
        return  angle - sign(angle) * pi
