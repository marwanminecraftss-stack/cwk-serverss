import hmac
import hashlib
import base64
import gzip
from io import BytesIO
import json
import random
import re
import shutil
import secrets
import string
import subprocess
import schedule
import threading
import time
from flask import Flask, Request, render_template, make_response, jsonify, request, redirect, abort, send_from_directory
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_bcrypt import Bcrypt
import os
import argparse
from urllib.parse import parse_qs
from datetime import datetime, timezone
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func
import discord_webhook

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=5000)
parser.add_argument('--debug', action='store_true')

args, _ = parser.parse_known_args()

app = Flask(__name__)

if not os.path.exists("flaskkey"):
	print("Creating new Flask secret key")
	with open("flaskkey", "w") as f:
		f.write(''.join(random.choice(string.ascii_letters + string.digits) for i in range(50)))
app.secret_key = open("flaskkey", "r").read()

bcrypt = Bcrypt(app)

login_manager = LoginManager(app)
login_manager.login_view = '/'

class Base(DeclarativeBase):
  pass

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///cardwarskingdom.db"
db = SQLAlchemy(model_class=Base)
db.init_app(app)

badcharaters = ['/', '\\', ':', '*', '?', '"', '<', '>', '|', ";", "%", "^", "&", "(", ")", "{", "}", "[", "]", ".", ",", "'", "`", "!", "$", "#", "@", "+", "="]

maintenance = False

@app.route("/static/version.txt")
def PersistVersion():
	with open("data/persist/version.txt", "r") as f:
		pc_version = f.read()
	with open("data/persist/android_version.txt", "r") as f:
		android_version = f.read()
  
	data = {
		"maintenance_mode": "yes" if maintenance else "no",
		"message": "Card Wars Kingdom is currently undergoing maintenance.\n\nPlease try again later.",
		"icon": "",
		"clickable": "yes",
		"android_version": android_version,
		"version": pc_version,
		"android_url": "https://github.com/shishkabob27/CardWarsKingdom/releases",
		"pc_url": "https://github.com/shishkabob27/CardWarsKingdom/releases",
	}
	return json.dumps(data)

class AdminActivity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    time = db.Column(db.Integer, nullable=False, default=int(time.time()))
    message = db.Column(db.String(8192), nullable=True)

def DiscordWebhookMessage(message):
	newActivity = AdminActivity(
		time=int(time.time()),
		message=message
	)
	db.session.add(newActivity)
	db.session.commit()
    
	if not os.path.exists("discordwebhookurl"):
		return
	else:
		with open("discordwebhookurl", "r") as f:
			url = f.read()
	try:
		webhook = discord_webhook.DiscordWebhook(url=url, content=message)
		webhook.execute()
	except:
		pass
 
class Admin(UserMixin, db.Model):
	username: Mapped[str] = mapped_column(db.String(80), primary_key=True, unique=True, nullable=False)
	password: Mapped[str] = mapped_column(db.String(80), nullable=False)
	rank: Mapped[int] = mapped_column(db.Integer, nullable=False)
 
	def get_id(self):
		return str(self.username)
 
@login_manager.user_loader
def load_user(user_id):
	return Admin.query.get(user_id)
 
@app.route("/admin", methods=['GET', 'POST'])
def AdminPage():
	if not Admin.query.first():
		randompassword = ''.join(random.choices(string.ascii_letters + string.digits, k=24))
		newAdmin = Admin(username="admin", password=bcrypt.generate_password_hash(randompassword).decode('utf-8'), rank=0)
		db.session.add(newAdmin)
		db.session.commit()
		print(f"Admin account created! Username: admin, Password: {randompassword}")
    
	if request.method == 'GET':
		if current_user.is_authenticated:
			if not isAdmin(current_user):
				return abort(404)
			return redirect("/admin/home")
		else:
			return render_template('admin_login.html')
	if request.method == 'POST':
		username = request.form['username']
		password = request.form['password']
		username = re.sub(r'[^a-zA-Z0-9]', '', username)
		db_user = Admin.query.filter_by(username=username).first()
		if db_user is None:
			return make_response("Invalid Username or Password!", 400)
		if not bcrypt.check_password_hash(db_user.password, password):
			return make_response("Invalid Password or Username!", 400)
		login_user(db_user, remember=True)
		return redirect("/admin")

def isAdmin(user):
	if not user.is_authenticated:
		return False
	db_user = Admin.query.filter_by(username=user.username).first()
	return db_user is not None

@login_required
@app.route("/admin/home")
def AdminHome():
	if not isAdmin(current_user):
		return abort(404)

	adminActivity = AdminActivity.query.order_by(AdminActivity.time).all()
	for log in adminActivity:
		log.time = datetime.fromtimestamp(log.time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
	adminActivity.reverse()
		
	return render_template('admin_home.html', Activity=adminActivity)

@login_required
@app.route("/admin/versions" , methods=['GET', 'POST'])
def AdminVersions():
	if not isAdmin(current_user):
		return abort(404)

	if request.method == 'GET':
		return render_template('admin_versions.html' , pc_version=open("data/persist/version.txt", "r").read(), android_version=open("data/persist/android_version.txt", "r").read())
	elif request.method == 'POST':
		form = request.form
		form = {k: v[0] if len(v) == 1 else v for k, v in form.items()}
  
		if "pc_version" not in form or form["pc_version"] == "":
			return make_response("Invalid PC version!", 400)
		if "android_version" not in form or form["android_version"] == "":
			return make_response("Invalid Android version!", 400)

		with open("data/persist/version.txt", "w") as f:
			f.write(form["pc_version"])
		with open("data/persist/android_version.txt", "w") as f:
			f.write(form["android_version"])
  
		return redirect("/admin/versions")

@login_required
@app.route("/admin/server")
def AdminServer():
	if not isAdmin(current_user):
		return abort(404)

	if not os.path.exists("backup"):
		os.makedirs("backup")
 
	last_backup_time = 0
	last_backup_file = ""
	for file in os.listdir("backup"):
		if file.endswith(".zip"):
			file_time = int(file.replace(".zip", "").replace("-", "").replace("_", ""))
			if file_time > last_backup_time:
				last_backup_time = file_time
				last_backup_file = file.replace(".zip", "")
   
	if last_backup_file == "":
		last_backup = "Never"
	else:
		last_backup = time_ago_string(datetime.strptime(last_backup_file, "%Y-%m-%d_%H-%M-%S"))
    
	return render_template('admin_server.html', last_backup=last_backup)

def time_ago_string(date_time):
    now = datetime.now()
    time_difference = now - date_time
    hours = time_difference.seconds // 3600
    minutes = (time_difference.seconds // 60) % 60

    if time_difference.days > 0:
        return f"{time_difference.days} days ago"
    elif hours > 0:
        return f"{hours} {'hour' if hours == 1 else 'hours'} ago"
    elif minutes > 0:
        return f"{minutes} {'minute' if minutes == 1 else 'minutes'} ago"
    else:
        return f"{time_difference.seconds} seconds ago"
    
@login_required
@app.route("/admin/server/backup")
def AdminBackup():
	if not isAdmin(current_user):
		return abort(404)

	backup = Backup()
	if not backup:
		return make_response("Failed to backup", 400)
	return redirect("/admin/server")

@login_required
@app.route("/admin/server/pull")
def AdminGitPull():
	if not isAdmin(current_user):
		return abort(404)

	output = subprocess.check_output(["git", "pull"])
	return make_response(output.decode("utf-8"), 200)

@login_required
@app.route("/admin/createadmin", methods=['GET', 'POST'])
def AdminCreateAdmin():
	if not isAdmin(current_user):
		return abort(404)

	if request.method == 'GET':
		return render_template('admin_createadmin.html')
	if request.method == 'POST':
		username = request.form['username']
		rank = request.form['rank']
		password = secrets.token_urlsafe(24)
		new_admin = Admin(username=username, password=bcrypt.generate_password_hash(password).decode('utf-8'), rank=int(rank))
		db.session.add(new_admin)
		db.session.commit()
		return "Admin created! Username: " + username + " Password: " + password

@login_required
@app.route("/admin/players")
def AdminPlayers():
	if not isAdmin(current_user):
		return abort(404)

	players = Player.query.all()
	players = [player.as_dict() for player in players]
	players = [player for player in players if player["game"] != None and player["leader_level"] != None]
	players = [player for player in players if not IsUserBanned(player["username"])]
	
	for player in players:
		player["last_online"] = datetime.fromtimestamp(player["last_online"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
		if player["multiplayer_name"] == None:
			player["multiplayer_name"] = GetNameFromSave(player["game"])

	sortQuery = request.args.get('sort')
	if sortQuery is not None:
		players = sorted(players, key=lambda player: player[sortQuery], reverse=True)
	else:
		players = players[::-1]
	
	return render_template('admin_players.html', players=players, player_count=len(players))

def GetNameFromSave(save):
	try:
		game = DecryptGameData(save)
	except Exception:
		return None
	if game is None:
		return None
	return game.get("MultiplayerPlayerName")

@login_required
@app.route("/admin/players/<player>")
def AdminPlayer(player):
	if not isAdmin(current_user):
		return abort(404)

	player_obj = Player.query.filter_by(username=player).first()
	if player_obj is None:
		return make_response("No player found!", 404)

	player = player_obj.as_dict()
	player["last_online"] = datetime.fromtimestamp(player["last_online"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
	
	game = None
	try:
		game = DecryptGameData(player["game"])
		if player["multiplayer_name"] == None and game:
			player["multiplayer_name"] = game.get("MultiplayerPlayerName")
	except Exception:
		game = None   
 
	if game is None:
		return render_template('admin_player.html', player=player) 

	battle_history = game.get("BattleHistory", [])
	battle_history.sort(key=lambda x: x.get("recordTime", 0))
	for battle in battle_history:
		if "recordTime" in battle:
			battle["recordTime"] = datetime.fromtimestamp(battle["recordTime"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
  
	if player["devicename"] is not None:
		player["devicename"] = re.sub(r'%[0-9A-Fa-f]{2}', lambda m: chr(int(m.group(0)[1:], 16)), player["devicename"])
  
	Inventory = game.get("Inventory")
	if Inventory is not None:
		Inventory = [item for item in Inventory if item.get("_T") == "CR"]
 
	return render_template('admin_player.html', player=player, is_banned=IsUserBanned(player["username"]), SoftCurrency=game.get("SoftCurrency", 0), HardCurrency=int(game.get("PaidHardCurrency", 0)) + int(game.get("FreeHardCurrency", 0)), PvpCurrency=game.get("PvpCurrency", 0), InstalledDate=datetime.fromtimestamp(game.get("InstalledDate", 0), tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'), PVPBanned=bool(game.get("Zxcvbnm", 0)), MultiplayerLevel=game.get("MultiplayerLevel", 0), InventorySpace=game.get("InventorySpace", 0), BattleHistory=battle_history, DeviceName=player["devicename"], Inventory=Inventory)

@login_required
@app.route("/admin/players/<player>/game")
def AdminPlayerGame(player):
	if not isAdmin(current_user):
		return abort(404)

	player_obj = Player.query.filter_by(username=player).first()
	if player_obj is None:
		return make_response("No player found!", 404)

	player = player_obj.as_dict()
	try:
		game = DecryptGameData(player["game"])
	except Exception:
		game = player["game"]

	if game is None:
		return make_response("No game found!", 404)

	return render_template('admin_player_game.html', game=game, player_id=player["username"])

@login_required
@app.route("/admin/players/<player>/game/edit", methods=['POST'])
def AdminPlayerGameEdit(player):
	if not isAdmin(current_user):
		return abort(404)

	player_obj = Player.query.filter_by(username=player).first()
	if player_obj is None:
		return make_response("No player found!", 404)

	game = request.form['player_game']
	player_obj.game = game
	db.session.commit()
	return redirect("/admin/players/" + player_obj.username)

def DecryptGameData(game):
	if game is None or game == b"" or game == b" ":
		return None
	try:
		if isinstance(game, str):
			input_data = game.encode("utf-8")
		else:
			input_data = game
		input_str = input_data.decode("utf-8")
		index = input_str.find("&data=")
		if index != -1:
			encoded_data = input_str[index + 6 :]
		else:
			encoded_data = input_str
		array = base64.b64decode(encoded_data)
		with gzip.GzipFile(fileobj=BytesIO(array), mode='rb') as gz:
			decoded_data = gz.read().decode("utf-8")
		decoded_data = decoded_data.replace(',}', '}').replace(',],', '],').replace(',]', ']').replace(',,', ',')
		return json.loads(decoded_data)
	except Exception:
		return None

@login_required
@app.route("/admin/players/<player>/<action>")
def AdminPlayerAction(player, action):
	if not isAdmin(current_user):
		return abort(404)
		
	if action == "ban":
		if not IsUserBanned(player):
			newban = Bans(username=player, bantype="userid", author=current_user.username, time=int(time.time()))
			db.session.add(newban)
			db.session.commit()
	elif action == "unban":
		if IsUserBanned(player):
			player_check = Bans.query.filter_by(username=player).first()
			if player_check:
				db.session.delete(player_check)
				db.session.commit()
	else:
		return make_response("Invalid action!", 400)

	return redirect("/admin/players/" + player)

def SystemBan(username):
	if not IsUserBanned(username):
		newban = Bans(username=username, bantype="userid", author="SYSTEM", time=int(time.time()))
		db.session.add(newban)
		db.session.commit()

@login_required
@app.route("/admin/ipban/<ip>/unban")
def AdminIPBan(ip):
	if not isAdmin(current_user):
		return abort(404)
	
	player_check = Bans.query.filter_by(username=ip).first()
	if player_check:
		db.session.delete(player_check)
		db.session.commit()
	return redirect("/admin/bannedips")
  
@login_required
@app.route("/admin/ipban", methods=['POST'])
def AdminIPBanAction():
	if not isAdmin(current_user):
		return abort(404)
	
	newban = Bans(username=request.form['ip'], bantype="ip", author=current_user.username, time=int(time.time()))
	db.session.add(newban)
	db.session.commit()
	return redirect("/admin/bannedips")

@login_required
@app.route("/admin/bannedplayers")
def AdminBannedPlayers():
	if not isAdmin(current_user):
		return abort(404)

	bans = Bans.query.filter_by(bantype="userid").all()
	bans = [ban.as_dict() for ban in bans]
	
	for ban in bans:
		p = Player.query.filter_by(username=ban["username"]).first()
		if p:
			ban["multiplayer_name"] = p.multiplayer_name or GetNameFromSave(p.game)
		else:
			ban["multiplayer_name"] = "Unknown"
		if ban["time"] is not None:
			ban["time"] = datetime.fromtimestamp(ban["time"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
 
	return render_template('admin_bannedplayers.html', bans=bans)

@login_required
@app.route("/admin/bannedips")
def AdminBannedIPs():
	if not isAdmin(current_user):
		return abort(404)

	bans = Bans.query.filter_by(bantype="ip").all()
	bans = [ban.as_dict() for ban in bans]
	for ban in bans:
		ban["time"] = datetime.fromtimestamp(ban["time"], tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
	return render_template('admin_bannedips.html', bans=bans)

@login_required
@app.route("/admin/maintenance")
def AdminMaintenance():
	if not isAdmin(current_user):
		return abort(404)
	return render_template('admin_maintenance.html', maintenance=maintenance)

@login_required
@app.route("/admin/maintenance/<action>")
def AdminMaintenanceAction(action):
	if not isAdmin(current_user):
		return abort(404)
	global maintenance
	if action == "enable":
		maintenance = True
	elif action == "disable":
		maintenance = False
	return redirect("/admin/maintenance")

@app.route("/admin/logout")
def AdminLogout():
	logout_user()
	return redirect("/admin")

def Backup():
	now = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
	os.makedirs("backup/" + now, exist_ok=True)
	if os.path.exists("instance/cardwarskingdom.db"):
		shutil.copy("instance/cardwarskingdom.db", f"backup/{now}/cardwarskingdom.db")
	if os.path.exists("data/persist"):
		shutil.copytree("data/persist", f"backup/{now}/persist")
	shutil.make_archive("backup/" + now, 'zip', "backup/" + now)
	shutil.rmtree("backup/" + now)
	return True

@login_required
@app.route("/admin/misc")
def AdminMisc():
	if not isAdmin(current_user):
		return abort(404)
	return render_template('admin_misc.html')

@login_required
@app.route("/admin/logs/delete/olderthan/<days>")
def AdminLogsDeleteOlderThan(days):
	if not isAdmin(current_user):
		return abort(404)
	days = int(days)
	seconds = days * 86400
	db.session.query(Logs).filter(Logs.time < int(time.time()) - seconds).delete()
	db.session.commit()
	return redirect("/admin/logs")

@login_required
@app.route("/admin/upsight/delete/olderthan/<days>")
def AdminUpsightDeleteOlderThan(days):
	if not isAdmin(current_user):
		return abort(404)
	days = int(days)
	seconds = days * 86400
	db.session.query(UpsightLogs).filter(UpsightLogs.time < int(time.time()) - seconds).delete()
	db.session.commit()
	return redirect("/admin/upsight")

@login_required
@app.route("/admin/logs", methods=['GET'])
def AdminLogs():
	if not isAdmin(current_user):
		return abort(404)    
	perpage = 20
	pagerequest = request.args.get('page', 1, type=int)
	query = request.args.get('query', '', type=str)
	if query != '':
		logs = db.paginate(db.select(Logs).filter(Logs.player == query).order_by(Logs.id.desc()), page=pagerequest, per_page=perpage)
	else:
		logs = db.paginate(db.select(Logs).order_by(Logs.id.desc()), page=pagerequest, per_page=perpage)
	return render_template('admin_logs.html', logs=logs, query=query)

@login_required
@app.route("/admin/upsight", methods=['GET'])
def AdminUpsight():
	if not isAdmin(current_user):
		return abort(404)
	perpage = 20
	pagerequest = request.args.get('page', 1, type=int)
	query = request.args.get('query', '', type=str)
	if query != '':
		logs = db.paginate(db.select(UpsightLogs).filter(UpsightLogs.player_id == query).order_by(UpsightLogs.id.desc()), page=pagerequest, per_page=perpage)
	else:
		logs = db.paginate(db.select(UpsightLogs).order_by(UpsightLogs.id.desc()), page=pagerequest, per_page=perpage)
	for log in logs.items:
		log.time = datetime.fromtimestamp(log.time)
	return render_template('admin_upsight.html', logs=logs, query=query)

class Bans(db.Model):
	username = db.Column(db.String(80), primary_key=True)
	bantype = db.Column(db.String(80), nullable=False)
	author = db.Column(db.String(80), nullable=True)
	time = db.Column(db.Integer, nullable=True, default=int(time.time()))
	
	def as_dict(self):
		return {c.name: getattr(self, c.name) for c in self.__table__.columns}

class Logs(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	date = db.Column(db.String(80), nullable=False)
	time = db.Column(db.String(80), nullable=False)
	player = db.Column(db.String(80), nullable=False)
	ip = db.Column(db.String(80), nullable=True)
	message = db.Column(db.String(8192), nullable=False)
 
class UpsightLogs(db.Model):
	id = db.Column(db.Integer, primary_key=True)
	player_id = db.Column(db.String(80), nullable=False)
	time = db.Column(db.Integer, nullable=False, default=int(time.time()))
	event = db.Column(db.String(80), nullable=False)
	action = db.Column(db.String(80), nullable=False)
	message = db.Column(db.String(1024), nullable=True)
 
def PlayerLog(ip:str, player:str, message:str):
	db_log = Logs(date=datetime.now().strftime("%Y-%m-%d"), time=datetime.now().strftime("%H:%M:%S"), player=player, ip=ip, message=message)
	db.session.add(db_log)
	db.session.commit()
 
def IPFromRequest(request:Request):
	ip = request.remote_addr
	if request.headers.getlist("X-Forwarded-For"):
		ip = request.headers.getlist("X-Forwarded-For")[0]
	return ip

class Player(db.Model):
	username = db.Column(db.String(80), primary_key=True, unique=True, nullable=False)
	game = db.Column(db.String(8192), nullable=True)
	multiplayer_name = db.Column(db.String(128), nullable=True)
	icon = db.Column(db.String(128), nullable=True)
	deck = db.Column(db.String(1024), nullable=True)
	deck_rank = db.Column(db.String(16), nullable=True)
	landscapes = db.Column(db.String(1024), nullable=True)
	helper_creature = db.Column(db.String(1024), nullable=True)
	leader = db.Column(db.String(128), nullable=True)
	leader_level = db.Column(db.Integer, nullable=True)
	allyboxspace = db.Column(db.Integer, nullable=True)
	level = db.Column(db.Integer, nullable=True)
	friends = db.Column(db.String(8192), nullable=True, default="[]")
	friend_requests = db.Column(db.String(8192), nullable=True, default="[]")
	last_online = db.Column(db.Integer, nullable=True, default=int(time.time()))
	helpcount = db.Column(db.Integer, nullable=True, default=0)
	anonymoushelpcount = db.Column(db.Integer, nullable=True, default=0)
	devicename = db.Column(db.String(128), nullable=True)
 
	def as_dict(self):
		return {c.name: getattr(self, c.name) for c in self.__table__.columns}

@app.route("/")
def Index():
	return "200 App server running"

@app.route("/persist/static/manifest.json")
def Manifest():
	with open("data/persist/manifest.json", "r") as f:
		return f.read()

@app.route("/persist/static/blueprints", methods=['GET'])
def Blueprints():
	data = []
	if os.path.exists("data/persist/blueprints"):
		for root, dirs, files in os.walk("data/persist/blueprints"):
			for file in files:
				with open(f"{root}/{file}", "r", encoding="utf-8", errors="ignore") as f_bp:
					data.append({
						"name": file.replace(".json", ""),
						"data": f_bp.read()
					})
	return jsonify(data)

@app.route("/persist/messages_received_ids")
def PersistMessagesReceivedIDs():
	return send_from_directory(directory="", path="data/persist/messages_received_ids.json", as_attachment=True, download_name="messages_received_ids.json")
	
@app.route("/persist/messages_get/<string:message>")
def PersistMessagesGet(message):
	if not os.path.exists(f"data/persist/messages/{message}.json"):
		return make_response("Message not found!", 404)
	return send_from_directory(directory="", path=f"data/persist/messages/{message}.json", as_attachment=True, download_name=f"{message}.json")

@app.route("/time/")
def Time():
	data = {
		"data": {
			"server_time": f"{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')}",
		}
	}
	return jsonify(data)

@app.route("/account/preAuth/")
def AccountPreAuth():
	data = {
		"data": {
			"nonce": os.urandom(32).hex()
		}
	}
	return jsonify(data)

@app.route("/account/gcAuth/", methods=['POST'])
def AccountGCAuth():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}
  
	player_id = clientData.get("player_id", "unknown")
	if InvalidUsername(player_id):
		return make_response("Invalid Username!", 400)

	if IsUserBanned(player_id, IPFromRequest(request)):
		return make_response("User is banned!", 400)
 
	db_user = Player.query.filter_by(username=player_id).first()
	isplayernew = False
 
	if db_user is None:
		db_user = Player(username=player_id)
		db.session.add(db_user)
		db.session.commit()
		isplayernew = True
		PlayerLog(ip=IPFromRequest(request), player=player_id, message="Created new player")
     
	data = {
		"data": {
			"user_id": player_id,
			"is_new": isplayernew
		}
	}
	return jsonify(data)

@app.route("/persist/getcc/")
def GetCountryCode():
	data = {
		"ip": request.headers.get("X-Forwarded-For", request.remote_addr),
		"country_code": "US"
	}
	return jsonify(data)

@app.route("/multiplayer/new_player/", methods=['POST'])
def MultiplayerNewPlayer():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}
 
	name = clientData.get("name", "Player")
	if InvalidUsername(name):
		return make_response("Invalid username!", 400)

	player_id = clientData.get("player_id")
	db_user = Player.query.filter_by(username=player_id).first() if player_id else None
	if db_user is None:
		return make_response("No player found!", 404)

	db_user.multiplayer_name = name
	db_user.icon = clientData.get("icon")
	db_user.deck_rank = clientData.get("deck_rank")
	db_user.landscapes = clientData.get("landscapes")
	db_user.helper_creature = clientData.get("helper_creature")
	db_user.leader = clientData.get("leader")
	db_user.leader_level = clientData.get("leader_level")
	db_user.allyboxspace = clientData.get("allyboxspace")
	db_user.level = clientData.get("level")
	db.session.commit()
 
	return jsonify({
		"success": True,
		"data": {
			"name": name,
			"icon": clientData.get("icon"),
			"leader": clientData.get("leader"),
			"level": str(clientData.get("leader_level", "1")),
			"trophies": "0"
		}
	})	

@app.route("/multiplayer/update_deck_name/", methods=['POST'])
def MultiplayerUpdateDeckName():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}

	name = clientData.get("name", "")
	if name and InvalidUsername(name):
		return make_response("Invalid username!", 400)

	player_id = clientData.get("player_id")
	db_user = Player.query.filter_by(username=player_id).first() if player_id else None
	if db_user is None:
		return make_response("No player found!", 404)

	db_user.deck_rank = clientData.get("deck_rank")
	db_user.landscapes = clientData.get("landscapes")
	db_user.helper_creature = clientData.get("helper_creature")
	db_user.leader = clientData.get("leader")
	
	new_leader_level = clientData.get("leader_level")
	if new_leader_level is not None:
		try:
			lvl_int = int(new_leader_level)
			if db_user.leader_level is None and lvl_int > 5:
				SystemBan(player_id)
			elif db_user.leader_level is not None and lvl_int > int(db_user.leader_level) + 10:
				SystemBan(player_id)
			db_user.leader_level = lvl_int
		except ValueError:
			pass
 
	db_user.allyboxspace = clientData.get("allyboxspace")
	db.session.commit()
 
	return jsonify({
		"success": True
	})
 
def get_hash_string(source_value, key):
	hmac_sha256 = hmac.new(key.encode('utf-8'), source_value.encode('utf-8'), hashlib.sha256)
	return hmac_sha256.hexdigest()

@app.route("/persist/user_action2/", methods=['POST'])
def UserAction2():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}
	
	player_id = clientData.get("player_id")
	if player_id and IsUserBanned(player_id, IPFromRequest(request)):
		return make_response("User is banned!", 400)
	
	if player_id:
		UpdateLastOnline(player_id)
 
	if "evt" in clientData and player_id:
		db_user = Player.query.filter_by(username=player_id).first()
		if db_user is None:
			return jsonify({"success": True})
		
		try:
			FreeHardCurrency = int(clientData.get("fr", 0))
			df = int(clientData.get("df", 0))
		except (ValueError, TypeError):
			FreeHardCurrency = 0
			df = 0
  
		finalamount = FreeHardCurrency + df
  
		key = "5424498w34tiowhtgoae0tu4iksdf4_4" + player_id + "650"
		handle = get_hash_string(player_id, key)

		data = {
			"success": True,
			"data": "{\"fields\": {\"level2\": " + str(finalamount) +  ", \"handle\": \"" + handle + "\"}}",
		}
	else:
		data = {
			"success": True,
		}
	
	return jsonify(data) 

def InvalidUsername(username):
	if not username:
		return True
	username = username.lower()
	for char in badcharaters:
		if char in username:
			return True
	if username == 'ua' or username == 'guest':
		return True
	return False

def IsUserBanned(username, ip=None):
	db_user = Bans.query.filter_by(username=username).first()
	if db_user is not None:
		return True
	if ip is None:
		return False
	db_ip = Bans.query.filter_by(username=ip).first()
	if db_ip is not None:
		return True
	return False

@app.route("/persist/game", methods=['GET', 'PUT'])
def PersistGame():
	username = request.headers.get("Player-Id")
	if not username:
		return make_response("No game found!", 404)
  
	if InvalidUsername(username):
		return make_response("Invalid Username!", 400)
	if IsUserBanned(username, IPFromRequest(request)):
		return make_response("No game found!", 404)

	if request.method == 'PUT':
		DeviceNameUser = Player.query.filter_by(username=username).first()
		devicename = request.headers.get("X-Nick-Description", "")
		if DeviceNameUser and DeviceNameUser.devicename is None or DeviceNameUser.devicename == b"":
			DeviceNameUser.devicename = devicename
			db.session.commit()
	
	UpdateLastOnline(username)
 
	if request.method == 'GET':
		db_user = Player.query.filter_by(username=username).first()
		if db_user is None or db_user.game is None or db_user.game == b"" or db_user.game == b" ":
			return make_response("No game found!", 404)
		return db_user.game

	if request.method == 'PUT':
		data = request.data
		db_user = Player.query.filter_by(username=username).first()
		if db_user is None:
			return make_response("No game found!", 404)
		db_user.game = data
		db.session.commit()
		return make_response("OK", 200)

def UpdateLastOnline(player_id):
	user = Player.query.filter_by(username=player_id).first()
	if user is None:
		return None
	user.last_online = int(time.time())
	db.session.commit()

def AllyBoxSpaceNotExceeded(player_id):
	user = Player.query.filter_by(username=player_id).first()
	if user is None:
		return None
	try:
		friends = json.loads(user.friends or "[]")
	except:
		friends = []
	friends_count = 0
	for friend in friends:
		if IsUserBanned(friend):
			continue
		friend_user = Player.query.filter_by(username=friend).first()
		if friend_user is None:
			continue
		friends_count += 1
	return friends_count < (user.allyboxspace or 50)

@app.route("/persist/friends/<string:player_id>")
def PersistFriends(player_id):
	UpdateLastOnline(player_id)
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 404)

	data = []
	try:
		player_friends = json.loads(db_user.friends or "[]")
	except:
		player_friends = []
 
	for friend in player_friends:
		if IsUserBanned(friend):
			continue
		allyinfo = GetAllyInfo(friend, True)
		if allyinfo is not None:
			data.append(allyinfo)
 
	return jsonify(data)

@app.route("/persist/friends_find_candidatesDW/", methods=['POST'])
def PersistFriendsFindCandidates():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}
  
	player_id = clientData.get("player_id")
	db_user = Player.query.filter_by(username=player_id).first() if player_id else None
	if db_user is None:
		return make_response("No player found!", 404)

	data = []
	try:
		player_friends = json.loads(db_user.friends or "[]")
	except:
		player_friends = []
 
	for friend in player_friends:
		if IsUserBanned(friend):
			continue
		allyinfo = GetAllyInfo(friend, True)
		if allyinfo is not None:
			data.append(allyinfo)
		
	try:
		lvl_range = int(clientData.get("level", 5))
	except:
		lvl_range = 5

	strangers = Player.query.filter(
		Player.username != player_id,
		Player.username.notin_(player_friends if player_friends else [""]),
		Player.helper_creature != None,
		Player.leader_level.between((db_user.leader_level or 1) - lvl_range, (db_user.leader_level or 1) + lvl_range)
	).order_by(func.random()).limit(3).all()
 
	for stranger in strangers:
		if IsUserBanned(stranger.username):
			continue
		allyinfo = GetAllyInfo(stranger.username, False)
		if allyinfo is not None:
			data.append(allyinfo)
	
	if data:
		data = random.sample(data, len(data))
  
	data2 = {
		"success": True,
		"data": json.dumps(data)
	}
	return jsonify(data2)

@app.route("/persist/friends_use_friendDW/", methods=['POST'])
def PersistFriendsUseFriend():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}
 
	db_ally = Player.query.filter_by(username=clientData.get("friendid")).first()
	if db_ally is None:
		return make_response("No player found!", 500)

	db_ally.helpcount = int(db_ally.helpcount or 0) + 1
	db.session.commit()
 
	return jsonify({"success": True})

@app.route("/persist/friends_use_playerDW/", methods=['POST'])
def PersistFriendsUsePlayer():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}
 
	db_stranger = Player.query.filter_by(username=clientData.get("userid")).first()
	if db_stranger is None:
		return make_response("No player found!", 404)

	db_stranger.anonymoushelpcount = int(db_stranger.anonymoushelpcount or 0) + 1
	db.session.commit()
 
	return jsonify({"success": True})

@app.route("/persist/friends_request_withmyinfoDW/", methods=['POST'])
def PersistFriendsRequestWithMyInfo():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}
  
	player_id = clientData.get("player_id")
	invite_id = clientData.get("invite_id", "").replace("_", "-")
	UpdateLastOnline(player_id)
	
	invite_user = Player.query.filter_by(username=invite_id).first()
	if invite_user is None:
		return make_response("No player found!", 400)

	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 400)

	try:
		inviteuserfr = json.loads(invite_user.friend_requests or "[]")
	except:
		inviteuserfr = []

	if AllyBoxSpaceNotExceeded(player_id) == False:
		return jsonify({"success": True, "info": "exceed me"})
  
	if AllyBoxSpaceNotExceeded(invite_id) == False:
		return jsonify({"success": True, "info": "exceed"})
 
	if player_id not in inviteuserfr:
		inviteuserfr.append(player_id)
		invite_user.friend_requests = json.dumps(inviteuserfr)
		db.session.commit()
		return jsonify({"success": True})
	else:
		return jsonify({"success": True, "info": "duplicate"})
  
def GetAllyInfo(player_id: str, isally: bool):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None or db_user.multiplayer_name is None:
		return None
	data = {
		"fields": {
			"user_id": db_user.username,
			"name": db_user.multiplayer_name,
			"icon": db_user.icon,
			"rankxp": db_user.leader_level,
			"helpcount": db_user.helpcount if db_user.helpcount is not None else "0",
			"anonymoushelpcount": db_user.anonymoushelpcount if db_user.anonymoushelpcount is not None else "0",
			"helpercreatureid": db_user.leader,
			"helpercreature": db_user.helper_creature,
			"landscapes": db_user.landscapes,
			"ally": "1" if isally else "0",
			"sincelastactivedate": str(int(time.time()) - (db_user.last_online or int(time.time())))	
		}
	}
	return data

@app.route("/persist/friends_all_requests_received/<string:player_id>", methods=['GET'])
def PersistFriendsAllRequestsReceived(player_id):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 400)

	data = []
	try:
		playerfriendrequests = json.loads(db_user.friend_requests or "[]")
	except:
		playerfriendrequests = []
 
	for friendrequest in playerfriendrequests:
		allyinfo = GetAllyInfo(friendrequest, False)
		if allyinfo is not None:
			data.append(allyinfo)
 
	return jsonify(data)

@app.route("/persist/friends_deny_request/<string:player_id>/<string:invite_id>", methods=['GET'])
def PersistFriendsDenyRequest(player_id, invite_id):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 400)

	UpdateLastOnline(player_id)	
	try:
		player_requests = json.loads(db_user.friend_requests or "[]")
		if invite_id in player_requests:
			player_requests.remove(invite_id)
		db_user.friend_requests = json.dumps(player_requests)
		db.session.commit()
	except:
		pass
	return jsonify({"success": True})
 
@app.route("/persist/friends_confirm_request_withmyinfoDW/", methods=['POST'])
def PersistFriendsConfirmRequestWithMyInfo():
	try:
		clientData = parse_qs(request.get_data().decode('utf-8'))
		clientData = {k: v[0] if len(v) == 1 else v for k, v in clientData.items()}
	except Exception:
		clientData = {}
  
	player_id = clientData.get("player_id")
	invite_id = clientData.get("invite_id")
	UpdateLastOnline(player_id)
 
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 400)

	if AllyBoxSpaceNotExceeded(player_id) == False:
		return jsonify({"success": True, "info": "exceed me"})
  
	if AllyBoxSpaceNotExceeded(invite_id) == False:
		return jsonify({"success": True, "info": "exceed"})

	try:
		player_requests = json.loads(db_user.friend_requests or "[]")
		if invite_id in player_requests:
			player_requests.remove(invite_id)
		db_user.friend_requests = json.dumps(player_requests)
	
		player_friends = json.loads(db_user.friends or "[]")
		if invite_id not in player_friends:
			player_friends.append(invite_id)
		db_user.friends = json.dumps(player_friends)
 
		friend_user = Player.query.filter_by(username=invite_id).first()
		if friend_user:
			friend_friends = json.loads(friend_user.friends or "[]")
			if player_id not in friend_friends:
				friend_friends.append(player_id)
			friend_user.friends = json.dumps(friend_friends)
	except:
		pass
 
	db.session.commit()
	return jsonify({"success": True})
 
@app.route("/persist/friends_remove/<string:player_id>/<string:invite_id>", methods=['GET'])
def PersistFriendsRemove(player_id, invite_id):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None:
		return make_response("No player found!", 400)

	try:
		player_friends = json.loads(db_user.friends or "[]")
		if invite_id in player_friends:
			player_friends.remove(invite_id)
		db_user.friends = json.dumps(player_friends)
 
		friend_user = Player.query.filter_by(username=invite_id).first()
		if friend_user:
			friend_friends = json.loads(friend_user.friends or "[]")
			if player_id in friend_friends:
				friend_friends.remove(player_id)
			friend_user.friends = json.dumps(friend_friends)
	except:
		pass
	
	db.session.commit()
	return jsonify({"success": True})

@app.route("/analytics/upsight", methods=['POST'])
def AnalyticsUpsight():
	headers = request.headers
	if headers.get("Player-Id") is None or headers.get("Event-Type") is None or headers.get("Event-Action") is None:
		return make_response("Bad request!", 400)

	message = request.get_data().decode('utf-8', errors="ignore")
	if message == "null":
		message = None

	newAnalytics = UpsightLogs(
		player_id=headers.get("Player-Id"),
		time=int(time.time()),
		event=headers.get("Event-Type"),
		action=headers.get("Event-Action"),
		message=message
	)
	db.session.add(newAnalytics)
	db.session.commit()
 
	if headers.get("Event-Action") == "detector":
		SystemBan(headers.get("Player-Id"))
 
	return make_response("OK", 200)

@app.route("/analytics/pvpmatch", methods=['POST'])
def AnalyticsPVPMatch():
	headers = request.headers
	if headers.get("Player-Id") is None:
		return make_response("Bad request!", 400)

	message = request.get_data().decode('utf-8', errors="ignore")
	if message == "null":
		message = None
 
	os.makedirs("data/persist/pvpmatches", exist_ok=True)
	try:
		with open("data/persist/pvpmatches/" + headers.get("Player-Id", "unknown") +"_"+ headers.get("Match-Id", "unknown") + ".json", "w", encoding="utf-8") as outfile:
			parsed_msg = json.loads(message)
			json.dump(parsed_msg, outfile, indent=4)
	except:
		pass
 
	return make_response("OK", 200)

@app.route("/dw_leaderboard/fetchentries/", methods=['POST'])
def LeaderboardFetchEntries():
	allplayers = Player.query.all()
	leaderboard = []
 
	for player in allplayers:
		if player.multiplayer_name is None or player.multiplayer_name == b"" or player.multiplayer_name == b" ":
			continue
		if (player.leader_level or 0) < 10:
			continue
		if time.time() - (player.last_online or 0) > 60 * 60 * 24 * 31:
			continue

		playerwins = 0
		try:
			playerwins = GetPlayerWins(player.username)
		except Exception:
			continue
  
		if playerwins is None or playerwins == 0:
			continue
		leaderboard.append({
			"playerid": player.username,
			"playername": player.multiplayer_name,
			"score": int(playerwins)
		})
 
	leaderboard = sorted(leaderboard, key=lambda k: k['score'], reverse=True)
	leaderboard = leaderboard[:50]
	
	for i in range(len(leaderboard)):
		leaderboard[i]["ranking"] = int(i+1)
 
	return jsonify({
		"success": True,
		"data" : f"{json.dumps(leaderboard)}"
	})
 
def GetPlayerWins(player_id):
	db_user = Player.query.filter_by(username=player_id).first()
	if db_user is None or db_user.game is None:
		return None

	if IsUserBanned(player_id):
		return None

	try:
		game = DecryptGameData(db_user.game)
	except Exception:
		return None

	if not game or game.get("Zxcvbnm"):
		return None

	currentSeason = ""
	if os.path.exists('data/persist/blueprints/db_PVPSeasons.json'):
		try:
			with open('data/persist/blueprints/db_PVPSeasons.json', 'r', encoding='utf-8') as f:
				seasons = json.load(f)
				seasons = list(filter(lambda x: "EndDate" in x, seasons))
				for season in seasons:
					if int(time.time()) < datetime.strptime(season["EndDate"], "%m/%d/%Y").timestamp():
						currentSeason = season["Season"]
						break
				if currentSeason == "" and seasons:
					currentSeason = seasons[-1]["Season"]
		except:
			pass
   
	if currentSeason and game.get("ActivePvpSeason") != currentSeason:
		return None

	if int(game.get("PvpPlayed", 0)) == 0:
		return None

	wins = 0
	for battle in game.get("BattleHistory", []):
		if battle.get("youWon") == True and (not currentSeason or battle.get("season") == currentSeason):
			wins += 1
	return wins

def run_scheduler():
    schedule.every(4).hours.do(Backup)
    while True:
        schedule.run_pending()
        time.sleep(1)
	
def Log(category, message):
	os.makedirs("data/persist/logs", exist_ok=True)
	date = datetime.now().strftime("%Y-%m-%d")
	time_str = datetime.now().strftime("%H:%M:%S")
	with open("data/persist/logs/" + date + ".txt", "a", encoding="utf-8") as f:
		log = f"{time_str} - [{category.upper()}] - {message} \n"
		f.write(log)

if __name__ == '__main__':
	Log("server", "Starting server...")
 
	if not os.path.exists("data/persist/version.txt"):
		with open("data/persist/version.txt", "w") as f:
			f.write("1.19.4")
	if not os.path.exists("data/persist/android_version.txt"):
		with open("data/persist/android_version.txt", "w") as f:
			f.write("1.19.4")
	
	with app.app_context():
		db.create_all()

	scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
	scheduler_thread.start()

	app.run(host='0.0.0.0', debug=args.debug, port=args.port)
