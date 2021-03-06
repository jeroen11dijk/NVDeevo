"""Main module"""
import math
import time
import numpy as np
from queue import Empty
from rlbot.utils.game_state_util import GameState, BallState, CarState, Physics, Vector3, Rotator, GameInfoState

from numba import jit
from rlbot.agents.base_agent import BaseAgent
from rlbot.agents.base_agent import SimpleControllerState
from rlbot.matchcomms.common_uses.reply import reply_to
from rlbot.matchcomms.common_uses.set_attributes_message import handle_set_attributes_message
from rlbot.utils.structures.game_data_struct import GameTickPacket

from boost import init_boostpads, update_boostpads
from custom_drive import CustomDrive as Drive
from defending import defending
from goal import Goal
from halfflip import HalfFlip
from jump_sim import get_time_at_height, get_time_at_height_boost
from kick_off import init_kickoff, kick_off
from rlutilities.linear_algebra import *
from rlutilities.mechanics import Dodge, AerialTurn
from rlutilities.simulation import Game, Car, obb, sphere
from steps import Step
from util import distance_2d, should_dodge, sign, velocity_2d, get_closest_big_pad, in_front_off_ball, get_intersect, \
    should_halfflip, line_backline_intersect, not_back

jeroens_magic_number = 5


class Hypebot(BaseAgent):
    """Main bot class"""

    def __init__(self, name, team, index):
        """Initializing all parameters of the bot"""
        super().__init__(name, team, index)
        Game.set_mode("soccar")
        self.info = Game(index, team)
        self.team = team
        self.index = index
        self.drive = None
        self.dodge = None
        self.halfflip = None
        self.controls = SimpleControllerState()
        self.kickoff = False
        self.prev_kickoff = False
        self.in_front_off_ball = False
        self.conceding = False
        self.kickoff_Start = None
        self.step = Step.Shooting
        self.time = 0
        self.my_goal = None
        self.their_goal = None
        self.teammates = []
        self.has_to_go = False
        self.closest_to_ball = False
        self.defending = False
        self.set_state = False
        self.ball_prediction_np = []

    def initialize_agent(self):
        """Initializing all parameters which require the field info"""
        self.my_goal = Goal(self.team, self.get_field_info())
        self.their_goal = Goal(1 - self.team, self.get_field_info())
        init_boostpads(self)
        """Setting all the mechanics to not none"""
        self.drive = Drive(self.info.my_car)
        self.dodge = Dodge(self.info.my_car)
        self.halfflip = HalfFlip(self.info.my_car)

    def get_output(self, packet: GameTickPacket) -> SimpleControllerState:
        """The main method which receives the packets and outputs the controls"""
        self.info.read_game_information(packet, self.get_field_info())
        self.in_front_off_ball = in_front_off_ball(self.info.my_car.position, self.info.ball.position,
                                                   self.my_goal.center)
        update_boostpads(self, packet)
        self.closest_to_ball = self.closest_to_the_ball()
        self.predict()
        self.time += self.info.time_delta
        if self.time > 5 and self.set_state:
            ball_state = BallState(Physics(location=Vector3(0, 5250, 250)))
            game_state = GameState(ball=ball_state)
            self.set_state = False
            self.set_game_state(game_state)
        dtype = [('physics', [('location', '<f4', 3), ('rotation', [('pitch', '<f4'), ('yaw', '<f4'), ('roll', '<f4')]),
                              ('velocity', '<f4', 3), ('angular_velocity', '<f4', 3)]), ('game_seconds', '<f4')]

        self.ball_prediction_np = np.ctypeslib.as_array(self.get_ball_prediction_struct().slices).view(dtype)[
                                  :self.get_ball_prediction_struct().num_slices]
        self.teammates = []
        for i in range(self.info.num_cars):
            if self.info.cars[i].team == self.team and i != self.index:
                self.teammates.append(i)
        self.time = packet.game_info.seconds_elapsed
        # self.handle_match_comms()
        self.prev_kickoff = self.kickoff
        self.kickoff = packet.game_info.is_kickoff_pause and distance_2d(self.info.ball.position, vec3(0, 0, 0)) < 100
        if self.kickoff and not self.prev_kickoff:
            # if not self.close_to_kickoff_spawn():
            #     return
            if len(self.teammates) > 0:
                if self.closest_to_ball:
                    init_kickoff(self)
                    self.has_to_go = True
                else:
                    self.drive.target = get_closest_big_pad(self).location
                    self.drive.speed = 1399
            else:
                init_kickoff(self)
                self.has_to_go = True
        if (self.kickoff or self.step == "Dodge2") and self.has_to_go:
            kick_off(self)
        elif self.kickoff and not self.has_to_go:
            self.drive.step(self.info.time_delta)
            self.controls = self.drive.controls
        else:
            if self.has_to_go:
                self.has_to_go = False
            self.get_controls()
        self.render_string(self.step.name)
        # Make sure there is no variance in kickoff setups
        if not packet.game_info.is_round_active:
            self.controls.steer = 0
        return self.controls

    def predict(self):
        """Method which uses ball prediction to fill in future data"""
        self.bounces = []
        self.ball_bouncing = False
        ball_prediction = self.get_ball_prediction_struct()
        if ball_prediction is not None:
            prev_ang_velocity = normalize(self.info.ball.angular_velocity)
            for i in range(ball_prediction.num_slices):
                prediction_slice = ball_prediction.slices[i]
                physics = prediction_slice.physics
                if physics.location.y * sign(self.team) > 5120:
                    self.conceding = True
                if physics.location.z > 180:
                    self.ball_bouncing = True
                    continue
                current_ang_velocity = normalize(
                    vec3(physics.angular_velocity.x, physics.angular_velocity.y, physics.angular_velocity.z))
                if physics.location.z < 125 and prev_ang_velocity != current_ang_velocity:
                    self.bounces.append((vec3(physics.location.x, physics.location.y, physics.location.z),
                                         prediction_slice.game_seconds - self.time))
                    if len(self.bounces) > 15:
                        return
                prev_ang_velocity = current_ang_velocity

    def closest_to_the_ball(self):
        dist_to_ball = math.inf
        for i in range(len(self.teammates)):
            teammate_car = self.info.cars[self.teammates[i]]
            if distance_2d(teammate_car.position, get_intersect(self, teammate_car)) < dist_to_ball:
                dist_to_ball = distance_2d(teammate_car.position, get_intersect(self, teammate_car))
        return distance_2d(self.info.my_car.position, get_intersect(self, self.info.my_car)) <= dist_to_ball

    def get_controls(self):
        """Decides what strategy to uses and gives corresponding output"""
        if self.step == Step.Steer or self.step == Step.Dodge_2 or self.step == Step.Dodge_1 or self.step == Step.Drive:
            print("GETCONTROLS")
            # self.time = 0
            # self.set_state = True
            self.step = Step.Shooting
        if self.step == Step.Shooting:
            target = get_intersect(self, self.info.my_car)
            self.drive.target = target
            self.drive.step(self.info.time_delta)
            self.controls = self.drive.controls
            t = time.time()
            can_dodge, simulated_duration, simulated_target = self.simulate(self.their_goal.center)
            # print(time.time() - t)
            if can_dodge:
                self.dodge = Dodge(self.info.my_car)
                self.turn = AerialTurn(self.info.my_car)
                self.dodge.duration = simulated_duration - 0.1
                self.dodge.direction = vec2(self.their_goal.center - simulated_target)

                target = vec3(vec2(self.their_goal.center)) + vec3(0, 0, jeroens_magic_number * simulated_target[2])
                self.dodge.preorientation = look_at(target - simulated_target, vec3(0, 0, 1))
                self.step = Step.Dodge
            if self.should_defend():
                self.step = Step.Defending
            elif not self.closest_to_ball or self.in_front_off_ball:
                self.step = Step.Rotating
            elif should_halfflip(self, target):
                self.step = Step.HalfFlip
                self.halfflip = HalfFlip(self.info.my_car)
            elif should_dodge(self, target):
                self.step = Step.Dodge
                self.dodge = Dodge(self.info.my_car)
                self.dodge.target = target
                self.dodge.duration = 0.1
        elif self.step == Step.Rotating:
            target = 0.5 * (self.info.ball.position - self.my_goal.center) + self.my_goal.center
            self.drive.target = target
            self.drive.speed = 1410
            self.drive.step(self.info.time_delta)
            self.controls = self.drive.controls
            in_position = not not_back(self.info.my_car.position, self.info.ball.position, self.my_goal.center)
            faster = self.closest_to_ball and in_position
            if len(self.teammates) == 0 and in_position:
                self.step = Step.Shooting
            elif len(self.teammates) == 1:
                teammate = self.info.cars[self.teammates[0]].position
                teammate_out_location = not_back(teammate, self.info.ball.position, self.my_goal.center)
                if teammate_out_location or faster:
                    self.step = Step.Shooting
            elif len(self.teammates) == 2:
                teammate1 = self.info.cars[self.teammates[0]].position
                teammate2 = self.info.cars[self.teammates[0]].position
                teammate1_out_location = not_back(teammate1, self.info.ball.position, self.my_goal.center)
                teammate2_out_location = not_back(teammate2, self.info.ball.position, self.my_goal.center)
                if teammate1_out_location and teammate2_out_location:
                    self.step = Step.Shooting
                elif (teammate1_out_location or teammate2_out_location) and faster or faster:
                    self.step = Step.Shooting
            if self.should_defend():
                self.step = Step.Defending
            elif should_halfflip(self, target):
                self.step = Step.HalfFlip
                self.halfflip = HalfFlip(self.info.my_car)
            elif should_dodge(self, target):
                self.step = Step.Dodge
                self.dodge = Dodge(self.info.my_car)
                self.dodge.target = target
                self.dodge.duration = 0.1

        elif self.step == Step.Defending:
            defending(self)
        elif self.step == Step.Dodge or self.step == Step.HalfFlip:
            halfflipping = self.step == Step.HalfFlip
            if halfflipping:
                self.halfflip.step(self.info.time_delta)
            else:
                self.dodge.step(self.info.time_delta)
            if (self.halfflip.finished if halfflipping else self.dodge.finished) and self.info.my_car.on_ground:
                self.step = Step.Shooting
            else:
                self.controls = (self.halfflip.controls if halfflipping else self.dodge.controls)
                if not halfflipping:
                    self.controls.boost = False
                self.controls.throttle = velocity_2d(self.info.my_car.velocity) < 500

    def handle_match_comms(self):
        try:
            msg = self.matchcomms.incoming_broadcast.get_nowait()
        except Empty:
            return
        if handle_set_attributes_message(msg, self, allowed_keys=['kickoff', 'prev_kickoff']):
            reply_to(self.matchcomms, msg)
        else:
            self.logger.debug('Unhandled message: {msg}')

    def render_string(self, string):
        """Rendering method mainly used to show the current state"""
        self.renderer.begin_rendering('The State')
        if self.step == Step.Dodge_1:
            self.renderer.draw_line_3d(self.info.my_car.position, self.dodge.target, self.renderer.black())
        self.renderer.draw_line_3d(self.info.my_car.position, self.drive.target, self.renderer.blue())
        if self.index == 0:
            self.renderer.draw_string_2d(20, 20, 3, 3, string, self.renderer.red())
        else:
            self.renderer.draw_string_2d(20, 520, 3, 3, string, self.renderer.red())
        self.renderer.end_rendering()

    # The miraculous simulate function
    # TODO optimize heavily in case I actually need it
    # Option one: estimate the time for the current height and look at that ball prediction.
    # If its heigher use that unless it gets unreachable and else compare with the lower one.
    # If duration_estimate = 0.8 and the ball is moving up there is not sense in even simulating it.
    # Might even lower it since the higher the duration estimate the longer the simulation takes.
    def simulate(self, global_target=None):
        lol = 0
        # Initialize the ball prediction
        # Estimate the probable duration of the jump and round it down to the floor decimal
        ball_prediction = self.get_ball_prediction_struct()
        if self.info.my_car.boost < 6:
            duration_estimate = math.floor(get_time_at_height(self.info.ball.position[2]) * 10) / 10
        else:
            adjacent = norm(vec2(self.info.my_car.position - self.info.ball.position))
            opposite = (self.info.ball.position[2] - self.info.my_car.position[2])
            theta = math.atan(opposite / adjacent)
            t = get_time_at_height_boost(self.info.ball.position[2], theta, self.info.my_car.boost)
            duration_estimate = (math.ceil(t * 10) / 10)
        # Loop for 6 frames meaning adding 0.1 to the estimated duration. Keeps the time constraint under 0.3s
        for i in range(6):
            # Copy the car object and reset the values for the hitbox
            car = Car(self.info.my_car)
            # Create a dodge object on the copied car object
            # Direction is from the ball to the enemy goal
            # Duration is estimated duration plus the time added by the for loop
            # preorientation is the rotation matrix from the ball to the goal
            # TODO make it work on both sides
            #  Test with preorientation. Currently it still picks a low duration at a later time meaning it
            #  wont do any of the preorientation.
            dodge = Dodge(car)
            prediction_slice = ball_prediction.slices[round(60 * (duration_estimate + i / 60))]
            physics = prediction_slice.physics
            ball_location = vec3(physics.location.x, physics.location.y, physics.location.z)
            # ball_location = vec3(0, ball_y, ball_z)
            dodge.duration = duration_estimate + i / 60
            if dodge.duration > 1.4:
                break

            if global_target is not None:
                dodge.direction = vec2(global_target - ball_location)
                target = vec3(vec2(global_target)) + vec3(0, 0, jeroens_magic_number * ball_location[2])
                dodge.preorientation = look_at(target - ball_location, vec3(0, 0, 1))
            else:
                dodge.target = ball_location
                dodge.direction = vec2(ball_location) + vec2(ball_location - car.position)
                dodge.preorientation = look_at(ball_location, vec3(0, 0, 1))
            # Loop from now till the end of the duration
            fps = 30
            for j in range(round(fps * dodge.duration)):
                lol = lol + 1
                # Get the ball prediction slice at this time and convert the location to RLU vec3
                prediction_slice = ball_prediction.slices[round(60 * j / fps)]
                physics = prediction_slice.physics
                ball_location = vec3(physics.location.x, physics.location.y, physics.location.z)
                dodge.step(1 / fps)

                T = dodge.duration - dodge.timer
                if T > 0:
                    if dodge.timer < 0.2:
                        dodge.controls.boost = 1
                        dodge.controls.pitch = 1
                    else:
                        xf = car.position + 0.5 * T * T * vec3(0, 0, -650) + T * car.velocity

                        delta_x = ball_location - xf
                        if angle_between(vec2(car.forward()), dodge.direction) < 0.3:
                            if norm(delta_x) > 50:
                                dodge.controls.boost = 1
                                dodge.controls.throttle = 0.0
                            else:
                                dodge.controls.boost = 0
                                dodge.controls.throttle = clip(0.5 * (200 / 3) * T * T, 0.0, 1.0)
                        else:
                            dodge.controls.boost = 0
                            dodge.controls.throttle = 0.0
                else:
                    dodge.controls.boost = 0

                car.step(dodge.controls, 1 / fps)
                succesfull = self.dodge_succesfull(car, ball_location, dodge)
                if succesfull is not None:
                    if succesfull:
                        return True, j / fps, ball_location
                    else:
                        break
        return False, None, None

    def dodge_succesfull(self, car, ball_location, dodge):
        batmobile = obb()
        batmobile.half_width = vec3(64.4098892211914, 42.335182189941406, 14.697200775146484)
        batmobile.center = car.position + dot(car.orientation, vec3(9.01, 0, 12.09))
        batmobile.orientation = car.orientation
        ball = sphere(ball_location, 93.15)
        b_local = dot(ball.center - batmobile.center, batmobile.orientation)

        closest_local = vec3(
            min(max(b_local[0], -batmobile.half_width[0]), batmobile.half_width[0]),
            min(max(b_local[1], -batmobile.half_width[1]), batmobile.half_width[1]),
            min(max(b_local[2], -batmobile.half_width[2]), batmobile.half_width[2])
        )

        hit_location = dot(batmobile.orientation, closest_local) + batmobile.center
        if norm(hit_location - ball.center) > ball.radius:
            return None
        # if abs(ball_location[2] - hit_location[2]) < 25 and hit_location[2] < ball_location[2]:
        if abs(ball_location[2] - hit_location[2]) < 25:
            if closest_local[0] > 35 and -12 < closest_local[2] < 12:
                hit_check = True
            else:
                print("local: ", closest_local)
                hit_check = True
        else:
            hit_check = False
        # Seems to work without angle_check. No clue why though
        angle_car_simulation = angle_between(car.orientation, self.info.my_car.orientation)
        angle_simulation_target = angle_between(car.orientation, dodge.preorientation)
        angle_check = angle_simulation_target < angle_car_simulation or angle_simulation_target < 0.1
        return hit_check

    def should_defend(self):
        """Method which returns a boolean regarding whether we should defend or not"""
        ball = self.info.ball
        car = self.info.my_car
        car_to_ball = ball.position - car.position
        in_front_of_ball = self.in_front_off_ball
        backline_intersect = line_backline_intersect(self.my_goal.center[1], vec2(car.position), vec2(car_to_ball))
        return (in_front_of_ball and abs(backline_intersect) < 2000) or self.conceding

    def close_to_kickoff_spawn(self):
        blue_one = distance_2d(self.info.my_car.position, vec3(-2048, -2560, 18)) < 10
        blue_two = distance_2d(self.info.my_car.position, vec3(2048, -2560, 18)) < 10
        blue_three = distance_2d(self.info.my_car.position, vec3(-256, -3840, 18)) < 10
        blue_four = distance_2d(self.info.my_car.position, vec3(256, -3840, 18)) < 10
        blue_five = distance_2d(self.info.my_car.position, vec3(0, -4608, 18)) < 10
        blue = blue_one or blue_two or blue_three or blue_four or blue_five
        orange_one = distance_2d(self.info.my_car.position, vec3(-2048, 2560, 18)) < 10
        orange_two = distance_2d(self.info.my_car.position, vec3(2048, 2560, 18)) < 10
        orange_three = distance_2d(self.info.my_car.position, vec3(-256, 3840, 18)) < 10
        orange_four = distance_2d(self.info.my_car.position, vec3(256, 3840, 18)) < 10
        orange_five = distance_2d(self.info.my_car.position, vec3(0, 4608, 18)) < 10
        orange = orange_one or orange_two or orange_three or orange_four or orange_five
        return orange or blue
