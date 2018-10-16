import math
import time

from LinearAlgebra import vec3, dot, clamp, sgn, normalize
from Simulation import Car, Input

class DoNothing:

    def __init__(self):

        self.controls = Input()
        self.finished = True

    def step(self, dt):

        return self.finished


class Jump:

    def __init__(self, duration):

        self.duration = duration
        self.controls = Input()

        self.timer = 0
        self.counter = 0

        self.finished = False

    def step(self, dt):

        self.controls.jump = 1 if self.timer < self.duration else 0

        if self.controls.jump == 0:
            self.counter += 1

        self.timer += dt

        if self.counter >= 2:
            self.finished = True

        return self.finished


class AirDodge:

    def __init__(self, car, duration = 0.0, target = None):

        self.car = car
        self.target = target
        self.controls = Input()

        self.jump = Jump(duration)

        if duration <= 0:
            self.jump.finished = True

        self.counter = 0
        self.state_timer = 0.0
        self.total_timer = 0.0

        self.finished = False

    def step(self, dt):

        recovery_time = 0.0 if (self.target is None) else 0.4

        if not self.jump.finished:

            self.jump.step(dt)
            self.controls = self.jump.controls

        else:

            if self.counter == 0:

                # double jump
                if self.target is None:
                    self.controls.roll = 0
                    self.controls.pitch = 0
                    self.controls.yaw = 0

                # air dodge
                else:
                    target_local = dot(self.target - self.car.pos, self.car.theta)
                    target_local[2] = 0;

                    direction = normalize(target_local)

                    self.controls.roll = 0
                    self.controls.pitch = -direction[0]
                    self.controls.yaw = sgn(self.car.theta[2,2]) * direction[1]

            elif self.counter == 2:

                self.controls.jump = 1

            elif self.counter >= 4:

                self.controls.roll = 0
                self.controls.pitch = 0
                self.controls.yaw = 0
                self.controls.jump = 0

            self.counter += 1
            self.state_timer += dt

        self.finished = (self.jump.finished and
                         self.state_timer > recovery_time and
                         self.counter >= 6)

        return self.finished


# Solves a piecewise linear (PWL) equation of the form
#
# a x + b | x | + (or - ?) c == 0
#
# for -1 <= x <= 1. If no solution exists, this returns
# the x value that gets closest
def solve_PWL(a, b, c):

    xp = c/(a+b) if abs(a+b) > 10e-6 else -1
    xm = c/(a-b) if abs(a-b) > 10e-6 else  1

    if xm <= 0 <= xp:
        if abs(xp) < abs(xm):
            clamp(xp,  0, 1)
        else:
            clamp(xm, -1, 0)
    else:
        if 0 <= xp:
            return clamp(xp,  0, 1)
        if xm <= 0:
            return clamp(xm, -1, 0)

    return 0


# w0: beginning step angular velocity (world coordinates)
# w1: beginning step angular velocity (world coordinates)
# theta: orientation matrix
# dt: time step
def aerial_rpy(w0, w1, theta, dt):

    # car's moment of inertia (spherical symmetry)
    J = 10.5

    # aerial control torque coefficients
    T = vec3(-400.0, -130.0, 95.0)

    # aerial damping torque coefficients
    H = vec3(-50.0, -30.0, -20.0)

    # get angular velocities in local coordinates
    w0_local = dot(w0, theta)
    w1_local = dot(w1, theta)

    # PWL equation coefficients
    a = [T[i] * dt / J for i in range(0, 3)]
    b = [-w0_local[i] * H[i] * dt / J for i in range(0, 3)]
    c = [w1_local[i] - (1 + H[i] * dt / J) * w0_local[i] for i in range(0, 3)]

    # RL treats roll damping differently
    b[0] = 0

    return vec3(
      solve_PWL(a[0], b[0], c[0]),
      solve_PWL(a[1], b[1], c[1]),
      solve_PWL(a[2], b[2], c[2])
    )


class AerialTurn:

    ALPHA_MAX = 9.0

    def periodic(self, x):
        return ((x - math.pi) % (2 * math.pi)) + math.pi

    def q(self, x):
        return 1.0 - (1.0 / (1.0 + 500.0 * x * x))

    def r(self, delta, v):
        return delta - 0.5 * sgn(v) * v * v / self.ALPHA_MAX

    def controller(self, delta, v, dt):
        ri = self.r(delta, v)

        alpha = sgn(ri) * self.ALPHA_MAX

        rf = self.r(delta - v * dt, v + alpha * dt)

        # use a single step of secant method to improve
        # the acceleration when residual changes sign
        if ri * rf < 0.0:
            alpha *= (2.0 * (ri / (ri - rf)) - 1)

        return alpha

    def find_landing_orientation(self, num_points):

        f = vec3(0, 0, 0)
        l = vec3(0, 0, 0)
        u = vec3(0, 0, 0)

        dummy = Car(self.car)
        for i in range(0, num_points):
            dummy.step(Input(), 0.0333)
            u = dummy.pitch_surface_normal()
            if norm(u) > 0.0 and i > 10:
                f = normalize(dummy.vel - dot(dummy.vel, u) * u)
                l = normalize(cross(u, f))
                self.found = True
                break

        if self.found:
            self.target = mat3(f[0], l[0], u[0],
                               f[1], l[1], u[1],
                               f[2], l[2], u[2])
        else:
            self.target = self.car.theta

    def __init__(self, car, target='Recovery', timeout=5.0):

        self.found = False
        self.car = car

        if target == 'Recovery':

            self.find_landing_orientation(200)

        else:

            self.target = target

        self.timeout = timeout

        self.epsilon_omega = 0.01
        self.epsilon_theta = 0.04

        self.controls = Input()

        self.timer = 0.0
        self.finished = False
        self.relative_rotation = vec3(0, 0, 0)
        self.geodesic_local = vec3(0, 0, 0)

    def step(self, dt):

        relative_rotation = dot(transpose(self.car.theta), self.target)
        geodesic_local = rotation_to_axis(relative_rotation)

        # figure out the axis of minimal rotation to target
        geodesic_world = dot(self.car.theta, geodesic_local)

        # get the angular acceleration
        alpha = vec3(
            self.controller(geodesic_world[0], self.car.omega[0], dt),
            self.controller(geodesic_world[1], self.car.omega[1], dt),
            self.controller(geodesic_world[2], self.car.omega[2], dt)
        )

        # reduce the corrections for when the solution is nearly converged
        for i in range(0, 3):
            error = abs(geodesic_world[i]) + abs(self.car.omega[i]);
            alpha[i] = self.q(error) * alpha[i]

        # set the desired next angular velocity
        omega_next = self.car.omega + alpha * dt

        # determine the controls that produce that angular velocity
        roll_pitch_yaw = aerial_rpy(self.car.omega, omega_next, self.car.theta, dt)

        self.controls.roll  = roll_pitch_yaw[0]
        self.controls.pitch = roll_pitch_yaw[1]
        self.controls.yaw   = roll_pitch_yaw[2]

        self.timer += dt

        if ((norm(self.car.omega) < self.epsilon_omega and
             norm(geodesic_world) < self.epsilon_theta) or
            self.timer >= self.timeout or self.car.on_ground):

            self.finished = True

        return self.finished


def solve_quadratic(a, b, c, interval=None):

    discriminant = b * b - 4 * a * c

    if discriminant < 0:

        return None

    else:

        x1 = (-b - sqrt(discriminant)) / (2 * a)
        x2 = (-b + sqrt(discriminant)) / (2 * a)

        if interval is None:
            return x1
        else:
            if interval[0] < x1 < interval[1]:
                return x1
            elif interval[0] < x2 < interval[1]:
                return x2
            else:
                return None


def look_at(direction, up=vec3(0, 0, 1)):

    f = normalize(direction)
    u = normalize(cross(f, cross(up, f)))
    l = normalize(cross(u, f))

    return mat3(f[0], l[0], u[0],
                f[1], l[1], u[1],
                f[2], l[2], u[2])


class Aerial:

    COUNTER = 0

    # states
    JUMP = 0
    AERIAL_APPROACH = 1

    # parameters
    jump_t = 0.25
    jump_dx = 300.0
    jump_dv = 500.0

    B = 1000.0  # boost acceleration
    g = -650.0  # gravitational acceleration
    a = 9.0     # maximum aerial angular acceleration

    def is_viable(self):

        # figure out what needs to be done in the maneuver
        self.calculate_course()

        # and if it requires an acceleration that the car
        # is unable to produce, then it is not viable
        return 0 <= self.B_avg < Aerial.B

    def calculate_course(self):

        v0 = self.car.vel
        dx = self.car.target - self.car.pos

        T = self.car.ETA - self.car.time

        if self.car.on_ground:

            v0 -= self.car.up() * Aerial.jump_dv
            dx -= self.car.up() * Aerial.jump_dx
            T  -= Aerial.jump_t

        self.H = dx - v0 * T - vec3(0, 0, 0.5 * Aerial.g * T * T)

        self.n = normalize(self.H)

        # estimate the time required to turn
        theta = angle_between(self.car.theta, look_at(self.n, self.up))
        ta = 0.5 * (2.0 * math.sqrt(theta / Aerial.a))

        # see if the boost acceleration needed to reach the target is achievable
        self.B_avg = 2.0 * norm(self.H) / ((T - ta) * (T - ta))

    def __init__(self, car, up=vec3(0, 0, 1)):

        self.car = car
        self.up = up

        self.controls = Input()

        self.action = None
        self.state = self.JUMP if car.on_ground else self.AERIAL_APPROACH

        self.H = vec3(0, 0, 0)
        self.n = vec3(0, 0, 0)
        self.B_avg = 0

        self.counter = 0
        self.state_timer = 0.0
        self.total_timer = 0.0
        self.boost_counter = 0.0

        self.finished = False

    def step(self, dt):

        old_state = self.state

        if self.state == self.JUMP:

            if self.action is None:
                self.action = AirDodge(car = self.car, duration = 0.2)

            jump_finished = self.action.step(dt)
            self.controls = self.action.controls

            if jump_finished:
                self.state = self.AERIAL_APPROACH

        elif self.state == self.AERIAL_APPROACH:

            if self.counter == 0:
                self.boost_counter = 0.0
                self.action = AerialTurn(car = self.car, target = self.car.theta)
                self.action.epsilon_omega = 0.0
                self.action.epsilon_theta = 0.0

            self.calculate_course()

            if norm(self.H) > 50.0:
                self.action.target = look_at(self.n, self.up)
            else:
                self.action.target = look_at(normalize(self.car.target-self.car.pos), self.up)

            self.action.step(dt)

            # use the controls from the aerial turn correction
            self.controls = self.action.controls

            use_boost = 0

            # and set the boost in a way that its duty cycle
            # approximates the desired average boost ratio
            if angle_between(self.action.target, self.car.theta) < 0.4:
                use_boost -= round(self.boost_counter)
                self.boost_counter += clamp(1.25 * (self.B_avg / Aerial.B), 0.0, 1.0)
                use_boost += round(self.boost_counter)

            self.controls.boost = 1 if use_boost else 0

        if self.state == old_state:
            self.counter += 1
            self.state_timer += dt
        else:
            self.counter = 0
            self.state_timer = 0.0

        self.total_timer += dt

        self.finished = (self.car.time >= self.car.ETA)

        return self.finished


class HalfFlip:

    def __init__(self, car, use_boost = False):

        self.car = car
        self.use_boost = use_boost
        self.controls = Input()

        behind = car.pos - 1000.0 * car.forward() - 0 * 120.0 * car.left()

        self.dodge = AirDodge(self.car, 0.10, target = behind)

        self.s = sgn(dot(self.car.omega, self.car.up()) + 0.01)

        self.counter = 0
        self.timer = 0.0

        self.finished = False

    def step(self, dt):

        boost_delay = 0.4
        stall_start = 0.50
        stall_end = 0.70
        timeout = 2.0

        self.dodge.step(dt)
        self.controls = self.dodge.controls

        if stall_start < self.timer < stall_end:
            self.controls.roll  =  0.0
            self.controls.pitch = -1.0
            self.controls.yaw   =  0.0

        if self.timer > stall_end:
            self.controls.roll  =  self.s
            self.controls.pitch = -1.0
            self.controls.yaw   =  self.s

        if self.use_boost and self.timer > boost_delay:
            self.controls.boost = 1
        else:
            self.controls.boost = 0

        self.timer += dt

        self.finished = (self.timer > timeout) or \
                        (self.car.on_ground and self.timer > 0.5)

        return self.finished



class Drive:

    __slots__ = ['car', 'target_pos', 'target_speed', 'controls', 'finished']

    def __init__(self, car, target_pos=vec3(0, 0, 0), target_speed=0):

        self.car = car
        self.target_pos = target_pos
        self.target_speed = target_speed
        self.controls = Input()

        self.finished = False

    def step(self, dt):

        max_throttle_speed = 1410
        max_boost_speed    = 2300

        # get the local coordinates of where the ball is, relative to the car
        # delta_local[0]: how far in front
        # delta_local[1]: how far to the left
        # delta_local[2]: how far above
        delta_local = dot(self.target_pos - self.car.pos, self.car.theta)

        # angle between car's forward direction and target position
        phi = math.atan2(delta_local[1], delta_local[0])

        self.controls.steer = clamp(2.5 * phi, -1.0, 1.0)

        if abs(phi) > 1.7:
            #self.controls.handbrake = 1
            self.controls.handbrake = 0

        if abs(phi) < 1.5:
            self.controls.handbrake = 0

        if self.controls.handbrake == 1:

            self.controls.boost = 0

        else:

            # forward velocity
            vf = dot(self.car.vel, self.car.forward())

            if vf < self.target_speed:
                self.controls.throttle = 1.0
                if self.target_speed > max_throttle_speed:
                    self.controls.boost = 1
                else:
                    self.controls.boost = 0
            else:
                if (vf - self.target_speed) > 75:
                    self.controls.throttle = -1.0
                else:
                    if self.car.up()[2] > 0.85:
                        self.controls.throttle = 0.0
                    else:
                        self.controls.throttle = 0.01
                self.controls.boost = 0
        #TODO add self.car.ETA
        #self.finished = (self.car.ETA - self.car.time) < 0

        return self.finished

# TODO
# TODO
class Wavedash:
# TODO
# TODO

    JUMP = 0
    AERIAL_TURN = 1
    AIR_DODGE = 2

    def __init__(self, car, target = None):

        self.car = car

        if target == None:
            self.direction = normalize(vec3(1, 0, 0))
        else:
            self.direction = dot(target - car.pos, car.theta)
            self.direction[2] = 0;
            self.direction = normalize(self.direction)

        self.controls = Input()
        self.controls.handbrake = True
        self.controls.throttle = 1

        self.action = None
        self.state = self.JUMP

        self.counter = 0
        self.state_timer = 0.0
        self.total_timer = 0.0

    def step(self, dt):

        turn_time = 0.5
        idle_time = 0.35

        old_state = self.state

        if self.state == self.JUMP:

            print('jump')

            if self.counter <= 2:
                self.controls.jump = 1
            elif self.counter <= 4:
                self.controls.jump = 0
            else:
                self.state = self.AERIAL_TURN

        elif self.state == self.AERIAL_TURN:

            print('turn')

            on_off = 0.15 if self.state_timer < turn_time else 0

            self.controls.roll  = on_off * -self.direction[1]
            self.controls.pitch = on_off *  self.direction[0]
            self.controls.yaw   = on_off * 0

            if self.state_timer > turn_time + idle_time:
                self.state = self.AIR_DODGE

        elif self.state == self.AIR_DODGE:

            print('dodge')

            if self.counter == 0:

                self.controls.roll  =  self.direction[1]
                self.controls.pitch = -self.direction[0]
                self.controls.yaw   = 0

            elif self.counter == 2:

                self.controls.jump = 1

            elif self.counter >= 4:

                self.controls.roll = 0
                self.controls.pitch = 0
                self.controls.yaw = 0
                self.controls.jump = 0

            self.controls.handbrake = True

        if self.state == old_state:
            self.counter += 1
            self.state_timer += dt
        else:
            self.counter = 0
            self.state_timer = 0.0

        self.total_timer += dt

        if self.state == self.AIR_DODGE and self.car.on_ground:
            return True
        else:
            return False
