import math

from RLUtilities.GameInfo import Car
from RLUtilities.LinearAlgebra import vec3, vec2, dot, normalize, norm


def eta_calculator(car: Car, target: vec3):
    boost_time = car.boost / 33
    speed = cap(dot(vec2(car.vel), vec2(normalize((target - car.pos)))) + 500 * boost_time ** 2, 1, 2300)
    distance = distance_2d(car.pos, target)
    if (distance / speed) < boost_time:
        speed = cap(dot(vec2(car.vel), vec2(normalize((target - car.pos)))) + 500 * (distance / speed) ** 2, 1, 2300)
        distance = distance_2d(car.pos, target)
        return 60 * distance / speed
    return 60 * distance / speed


def distance_2d(a, b):
    return norm(vec2(a-b))


def angle_2d(target_location, object_location):
    difference = target_location - object_location
    return math.atan2(difference[1], difference[0])


def velocity_2d(vel):
    return norm(vec2(vel))


def line_backline_intersect(agent, origin, direction):
    center = vec2(agent.info.their_goal.center)
    mult = (center[1] - origin[1])/direction[1]
    return (origin + mult*direction)[0]


def get_closest_pad(agent):
    pads = agent.info.boost_pads
    pad = None
    distance = math.inf
    for i in range(len(pads)):
        if distance_2d(agent.info.my_car.pos, pads[i].pos) < distance:
            distance = distance_2d(agent.info.my_car.pos, pads[i].pos)
            pad = pads[i]
    return pad


def get_closest_small_pad(agent):
    pad = None
    distance = math.inf
    for i in range(agent.get_field_info().num_boosts):
        current = agent.get_field_info().boost_pads[i]
        current_pos = vec3(current.location.x, current.location.y, current.location.z)
        if not current.is_full_boost and distance_2d(agent.info.my_car.pos, current_pos) < distance:
            distance = distance_2d(agent.info.my_car.pos, current_pos)
            pad = current
    return pad


def quad(a, b, c):
    inside = (b ** 2) - (4 * a * c)
    if inside < 0 or a == 0:
        return 0.1
    else:
        n = ((-b - math.sqrt(inside)) / (2 * a))
        p = ((-b + math.sqrt(inside)) / (2 * a))
        if p > n:
            return p
        return n


def remap_angle(angle):
    if angle < -math.pi:
        angle += 2 * math.pi
    if angle > math.pi:
        angle -= 2 * math.pi
    return angle


def sign(x):
    if x <= 0:
        return -1
    else:
        return 1


def cap(x, low, high):
    if x < low:
        return low
    elif x > high:
        return high
    else:
        return x


def z0(vector):
    return vec3(vector[0], vector[1], 0)


def time_z(ball):
    rate = 0.97
    return quad(-325, ball.vel[2] * rate, ball.pos[2] - 92.75)


def in_front_of_ball(agent):
    our_goal = agent.info.my_goal.center
    return distance_2d(agent.info.ball.pos, our_goal) < distance_2d(agent.info.my_car.pos, our_goal)


def boost_needed(initial_speed, goal_speed):
    p1 = 6.31e-06
    p2 = 0.010383
    p3 = 1.3183
    boost_initial = p1 * initial_speed ** 2 + p2 * initial_speed + p3
    boost_goal = p1 * goal_speed ** 2 + p2 * goal_speed + p3
    return boost_goal - boost_initial


def is_reachable(agent, location, eta):
    distance = distance_2d(agent.info.my_car.pos, location)
    speed = distance / (eta - agent.time)
    if speed < 2300:
        if speed < 1399:
            return True
        return agent.info.my_car.boost > boost_needed(velocity_2d(agent.info.my_car.vel), speed)


def render_string(agent, string):
    agent.renderer.begin_rendering()
    agent.renderer.draw_line_3d(agent.info.my_car.pos, agent.drive.target_pos, agent.renderer.black())
    agent.renderer.draw_string_2d(20, 20, 3, 3, string, agent.renderer.red())
    agent.renderer.end_rendering()



