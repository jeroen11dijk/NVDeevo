""""Module that handles the defending strategy"""
import math

from rlutilities.linear_algebra import normalize, rotation, vec3, vec2, dot
from rlutilities.mechanics import Dodge
from util import line_backline_intersect, cap, distance_2d, sign, get_speed, can_dodge


def defending(agent):
    """"Method that gives output for the defending strategy"""
    target = defending_target(agent)
    agent.drive.target = target
    agent.drive.speed = get_speed(agent, target)
    agent.drive.step(agent.fps)
    agent.controls = agent.drive.controls
    if can_dodge(agent, target):
        agent.step = "Dodge"
        agent.dodge = Dodge(agent.info.my_car)
        agent.dodge.duration = 0.1
        agent.dodge.target = target
    if not agent.defending:
        agent.step = "Catching"


def defending_target(agent):
    """"Method that gives the target for the shooting strategy"""
    ball = agent.info.ball
    car = agent.info.my_car
    car_to_ball = ball.location - car.location
    backline_intersect = line_backline_intersect(agent.my_goal.center[1], vec2(car.location), vec2(car_to_ball))
    if backline_intersect < 0:
        target = agent.my_goal.center - vec3(2000, 0, 0)
    else:
        target = agent.my_goal.center + vec3(2000, 0, 0)
    target_to_ball = normalize(ball.location - target)
    # Subtract target to car vector
    difference = target_to_ball - normalize(car.location - target)
    error = cap(abs(difference[0]) + abs(difference[1]), 1, 10)

    goal_to_ball_2d = vec2(target_to_ball[0], target_to_ball[1])
    test_vector_2d = dot(rotation(0.5 * math.pi), goal_to_ball_2d)
    test_vector = vec3(test_vector_2d[0], test_vector_2d[1], 0)

    distance = cap((40 + distance_2d(ball.location, car.location) * (error ** 2)) / 1.8, 0, 4000)
    location = ball.location + vec3((target_to_ball[0] * distance), target_to_ball[1] * distance, 0)

    # this adjusts the target based on the ball velocity perpendicular to the direction we're trying to hit it
    multiplier = cap(distance_2d(car.location, location) / 1500, 0, 2)
    distance_modifier = cap(dot(test_vector, ball.velocity) * multiplier, -1000, 1000)
    location += vec3(test_vector[0] * distance_modifier, test_vector[1] * distance_modifier, 0)

    # another target adjustment that applies if the ball is close to the wall
    extra = 3850 - abs(location[0])
    if extra < 0:
        location[0] = cap(location[0], -3850, 3850)
        location[1] = location[1] + (-sign(agent.team) * cap(extra, -800, 800))
    return location
