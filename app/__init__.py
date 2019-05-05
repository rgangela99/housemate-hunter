import json
import os
from enum import Enum
from flask import Flask, request
from collections import defaultdict
from .db import db, User, Location
from .state_abbrev import state_abbrev
import geocoder
from math import radians, sin, cos, sqrt, asin

GOOGLE_API_KEY = os.environ['GOOGLE_API_KEY']

app = Flask(__name__)

app.config['SQLALCHEMY_DATABASE_URI'] = os.environ['DATABASE_URL']
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ECHO'] = True

db.init_app(app)
with app.app_context():
    db.create_all()


@app.route('/')
def root():
    return "Hello world!"


@app.route('/api/users/', methods=['GET'])
def get_users():
    users = User.query.all()
    data = [user.serialize() for user in users]
    return json.dumps({"success": True, "data": data}), 200


@app.route('/api/users/', methods=['POST'])
def post_user():
    body = json.loads(request.data.decode('utf-8'))
    device_id = body.get('device_id')
    if User.query.filter_by(device_id=device_id).first():
        return json.dumps({"success": False, "error": "User already exists"}), 400
    city = body.get('city').title()
    state = body.get('state').capitalize()
    if state in state_abbrev:
        state = state_abbrev[state]
    state = state.upper()
    address = body.get('address')
    location = find_location(city, state, address)
    user = User(
        device_id=device_id,
        name=body.get('name'),
        netid=body.get('netid'),
        grad_year=body.get('grad_year'),
        age=body.get('age'),
        gender=body.get('gender'),
        sleep_time=body.get('sleep_time'),
        cleanliness=body.get('cleanliness'),
        bio=body.get('bio'),
        email=body.get('email'),
        phone=body.get('phone'),
        location=location.serialize()['id']
    )
    update_nearby(user)
    db.session.add(user)
    db.session.commit()
    res = {"success": True, "data": user.serialize()}
    return json.dumps(res), 201


@app.route('/api/user/<string:device_id>/', methods=['GET'])
def get_user(device_id):
    user = User.query.filter_by(device_id=device_id).first()
    if not user:
        return json.dumps({"success": False, "error": "User not found"}), 404
    data = user.serialize()
    return json.dumps({"success": True, "data": data}), 200


@app.route('/api/user/<string:device_id>/', methods=['DELETE'])
def delete_user(device_id):
    user = User.query.filter_by(device_id=device_id).first()
    if not user:
        return json.dumps({"success": False, "error": "User not found"}), 404
    db.session.delete(user)
    db.session.commit()
    return json.dumps({"success": True, "data": user.serialize()}), 200


@app.route('/api/nearby/<string:device_id>/', methods=['GET'])
def get_nearby_users(device_id):
    user = User.query.filter_by(device_id=device_id).first()
    if not user:
        return json.dumps({"success": False, "error": "User not found"}), 404
    nearby = user.location.nearby_users
    data = [u.serialize() for u in nearby if u.device_id != device_id]
    return json.dumps({"success": True, "data": data}), 200


@app.route('/api/matches/<string:device_id>/', methods=['GET'])
def get_matches(device_id):
    user = User.query.filter_by(device_id=device_id).first()
    if not user:
        return json.dumps({"success": False, "error": "User not found"}), 404
    nearby = user.location.nearby_users
    user_sims = []
    for u in nearby:
        if u.device_id == device_id:
            continue
        user_sims.append((compute_sim(user, u), u))
    matches = sorted(user_sims, key=lambda x: x[0], reverse=True)[:10]
    data = [{"similarity": sim, "user": u.serialize()} for sim, u in matches]
    return json.dumps({"success": True, "data": data}), 200


def find_location(city, state, address):
    g = geocoder.google(
        "{}, {}, {}".format(address, city, state))
    if not g.ok:
        return json.dumps({"success": False, "error": "Invalid location"}), 400
    latitude, longitude = g.latlng
    location = Location.query.filter_by(
        latitude=latitude, longitude=longitude).first()
    if not location:
        location = Location(
            city=city,
            state=state,
            address=address,
            latitude=latitude,
            longitude=longitude
        )
        db.session.add(location)
        db.session.commit()
        add_nearby_users(location)
    return location


def add_nearby_users(location):
    loc = location.serialize()
    users = User.query.all()
    for user in users:
        user_lat = user.serialize()['location']['latitude']
        user_long = user.serialize()['location']['longitude']
        dist = find_distance(
            loc['latitude'], loc['longitude'], user_lat, user_long)
        # check if user is within 20 miles
        if dist <= 32:
            location.nearby_users.append(user)
    db.session.commit()


def update_nearby(user):
    locations = Location.query.all()
    user_lat = user.serialize()['location']['latitude']
    user_long = user.serialize()['location']['longitude']
    for location in locations:
        loc = location.serialize()
        dist = find_distance(
            loc['latitude'], loc['longitude'], user_lat, user_long)
        if dist <= 32:
            location.nearby_users.append(user)
    db.session.commit()


def find_distance(lat1, long1, lat2, long2):
    """Source: https://rosettacode.org/wiki/Haversine_formula#Python"""
    EARTH_RADIUS = 6372.8
    dLat = radians(lat2 - lat1)
    dLong = radians(long2 - long1)
    lat1 = radians(lat1)
    lat2 = radians(lat2)
    a = sin(dLat / 2)**2 + cos(lat1) * cos(lat2) * sin(dLong / 2)**2
    c = 2 * asin(sqrt(a))
    return EARTH_RADIUS * c


def compute_sim(user1, user2):
    """ Finds the similarity between user1 and user2 based on graduation year, 
    age, gender, sleep schedule, cleanliness, and distance """
    year_weight = 0.15
    age_weight = 0.05
    gender_weight = 0.275
    sleep_weight = 0.175
    cleanliness_weight = 0.175
    location_weight = 0.175
    sim = year_weight * year_sim(user1, user2)
    sim += age_weight * age_sim(user1, user2)
    sim += gender_weight * gender_sim(user1, user2)
    sim += sleep_weight * sleep_sim(user1, user2)
    sim += cleanliness_weight * cleanliness_sim(user1, user2)
    sim += location_weight * location_sim(user1, user2)
    return sim


def year_sim(user1, user2):
    diff = abs(user1.grad_year - user2.grad_year)
    if diff == 0:
        return 1
    elif user1.grad_year <= 2019 and user2.grad_year <= 2019:
        if diff <= 2:
            return 0.75
        elif diff == 3:
            return 0.5
        elif diff == 4:
            return 0.25
        return 0
    elif diff == 1:
        return 0.67
    elif diff == 2:
        return 0.33
    return 0


def age_sim(user1, user2):
    diff = abs(user1.age - user2.age)
    if diff == 0:
        return 1
    elif diff == 1:
        return 0.75
    elif diff == 2:
        return 0.5
    elif diff == 3:
        return 0.25
    return 0


def gender_sim(user1, user2):
    return user1.gender == user2.gender


def sleep_sim(user1, user2):
    diff = abs(user1.sleep_time - user2.sleep_time)
    if diff == 0:
        return 1
    elif diff == 1:
        return 0.25
    return 0


def cleanliness_sim(user1, user2):
    return user1.cleanliness == user2.cleanliness


def location_sim(user1, user2):
    loc1 = user1.location
    loc2 = user2.location
    dist = find_distance(loc1.latitude, loc1.longitude,
                         loc2.latitude, loc2.longitude)
    if dist == 0:
        return 1
    elif dist <= 5:
        return 0.9
    elif dist <= 10:
        return 0.775
    elif dist <= 15:
        return 0.625
    elif dist <= 20:
        return 0.45
    elif dist <= 25:
        return 0.25
    return 0
