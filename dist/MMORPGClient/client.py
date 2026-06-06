import asyncio
import json
import math
import socket
import ssl
import struct
import time
import uuid
import traceback
import pygame
import sys
from aioquic.asyncio import connect, QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import HandshakeCompleted, StreamDataReceived
from collections import deque

IMAGE = 'men-stands.png'
MAP_PATH = "new_map.txt"
INVENTORY = "inventory.png"
KNIFE = "knife.png"
GUN = "gun.png"
HEALTH = "heal.png"
STRENGTH = "strength.png"
SHIELD = "shield.png"
BOW = "bow.png"

ENEMIES = {}
ENEMY_BULLETS = {}

SPEED = 180
SPRINT_SPEED = 360
CROUCH_SPEED = 60

WIDTH = 1200
HEIGHT = 700

SERVER_TIMEOUT = 6.0
PING_INTERVAL = 2.0

UP = 1 << 0
LEFT = 1 << 1
DOWN = 1 << 2
RIGHT = 1 << 3
SHOOT = 1 << 4
SPRINT = 1 << 5
CROUCH = 1 << 6

DIR_MASK = UP | LEFT | DOWN | RIGHT
MOVE_MASK = DIR_MASK | CROUCH | SPRINT

MAP_WIDTH = 1920 * 40 # 76800 pixels
MAP_HEIGHT = 1080 * 40 # 43200 pixels
MAP_HALF_WIDTH = MAP_WIDTH // 2
MAP_HALF_HEIGHT = MAP_HEIGHT // 2

PLAYER_WIDTH = 37
PLAYER_HEIGHT = 56

HP_BAR_WIDTH = 40
HP_BAR_HEIGHT = 6
HP_BAR_OFFSET_Y = 10

TILE_SIZE =40
TILE_DEFS = {
    '.': ("ground.png", True),
    '#': ("lava.png", True),      # lava is walkable - damages but doesn't block
    'T': ("tree.png", False),     # tree blocks movement

    '←': ("grnd_lava_left.png", True),
    '→': ("grnd_lava_right.png", True),
    '↑': ("grnd_lava_up.png", True),
    '↓': ("grnd_lava_down.png", True),

    '↖': ("grnd_lava_up_left.png", True),
    '↗': ("grnd_lava_up_right.png", True),
    '↘': ("grnd_lava_down_right.png", True),
    '↙': ("grnd_lava__left_down.png", True),

    '⇦': ("grnd_lava_up_right_down.png", True),
    '⇨': ("grnd_lava_up_left_down.png", True),
    '⇧': ("grnd_lava_left_down_right.png", True),
    '⇩': ("grnd_lava_left_up_right.png", True),
}
TILE_DICT = {}
HALF_TILE = TILE_SIZE // 2

LAVA_DAMAGE = 2.5
LAVA_INTERVAL = 0.5

SEQ_BITS = 16
SEQ_MAX = 1 << SEQ_BITS
SEQ_HALF = SEQ_MAX >> 1

MANAGER_IP = None
MANAGER_PORT = None
MANAGER_HOST = None

MSG_HISTORY = list()
IN_CHAT = False
CHAT_EVENTS = []
CHAT_STATE = {
    "focused": False,
    "current_input": "",
    "scroll_offset": 0
}
UNREAD_MSGS = 0

FIXED_DT = 1.0 / 60.0

RECONNECT_INFO = None
LOADED_WORLD = {}
CURRENT_CLIENT = None

ERROR_LOG_PATH = "client_errors.log"


def _log_obfuscated_error(context, exc):
    """Write errors to a local log file without exposing console output."""
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(ERROR_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {context}: {repr(exc)}\n")
            f.write(traceback.format_exc())
            f.write("\n")
    except Exception:
        pass

inventory = [0, 0, 0, 0, 0, 0]
BOT_MODE_ACTIVE = False

# ===== MEN =====
MEN_SPRITES = {
    'down': [
        pygame.image.load("./men_down_walk_leftleg.png"),
        pygame.image.load("./men_down_walk_rightleg.png")
    ],
    'down_idle': pygame.image.load("./men_down_idel.png"),

    'left': [
        pygame.image.load("./men_walk_left.png"),
        pygame.image.load("./men_walk_left_2.png")
    ],
    'left_idle': pygame.image.load("./men_left_idle.png"),

    'right': [
        pygame.image.load("./men_walk_right (3).png"),
        pygame.image.load("./men_walk_right (3).png")

    ],
    'right_idle': pygame.image.load("./men_right_idle.png"),

    'up': [
        pygame.image.load("./men_walk_up_1.png"),
        pygame.image.load("./men_walk_up_2.png")
    ]
}

# ===== WOMEN =====
WOMEN_SPRITES = {
    'down': [
        pygame.image.load("./women_walk_down_1.png"),
        pygame.image.load("./women_walk_down_2.png")
    ],
    'down_idle': pygame.image.load("./women_walk_down_idle.png"),

    'left': [
        pygame.image.load("./women_walk_left_1.png"),
        pygame.image.load("./women_walk_left_2.png")
    ],
    'left_idle': pygame.image.load("./women_walk_left_idle.png"),

    'right': [
        pygame.image.load("./women_walk_right_1.png"),
        pygame.image.load("./women_walk_right_2.png")
    ],
    'right_idle': pygame.image.load("./women_walk_right_idle.png"),

    'up': [
        pygame.image.load("./women_walk_up_1.png"),
        pygame.image.load("./women_walk_up_2.png")
    ],
    'up_idle': pygame.image.load("./women_walk_up_idle.png")
}




SELF_SPRITES_LIST = {
    'down': [
        pygame.image.load('Myspidy_walk_down_1.png'),
        pygame.image.load('Myspidy_walk_down_2.png')
    ],
    'down_idle': pygame.image.load('Myspidy_walk_down_idle.png'),

    'left': [
        pygame.image.load('Myspidy_walk_left_1 (1).png'),
        pygame.image.load('Myspidy_walk_left_2.png')
    ],
    'left_idle': pygame.image.load('Myspidy_walk_left_idle (1).png'),

    'right': [
        pygame.image.load('Myspidy_walk_right_1.png'),
        pygame.image.load('Myspidy_walk_right_2 (1).png')
    ],
    'right_idle': pygame.image.load('Myspidy_walk_right_idle (1).png'),

    'up': [
        pygame.image.load('Myspidy_walk_up_1 (1).png'),
        pygame.image.load('Myspidy_walk_up_2.png')
    ],
    'up_idle': pygame.image.load('Myspidy_walk_up_idle.png')
}

#=====================OTHER SPRITES=====================

OTHER_SPRITES_LIST = {
    'down': [
        pygame.image.load('./spidy_walk_down_1.png'),
        pygame.image.load('./spidy_walk_down_2.png')
    ],
    'down_idle': pygame.image.load('./spidy_walk_down_idle.png'),

    'left': [
        pygame.image.load('./spidy_walk_left_1.png'),
        pygame.image.load('./spidy_walk_left_2.png')
    ],
    'left_idle': pygame.image.load('./spidy_walk_left_idle.png'),

    'right': [
        pygame.image.load('./spidy_walk_right_1.png'),
        pygame.image.load('./spidy_walk_right_2.png')
    ],
    'right_idle': pygame.image.load('./spidy_walk_right_idle.png'),

    'up': [
        pygame.image.load('./spidy_walk_up_1.png'),
        pygame.image.load('./spidy_walk_up_2.png')
    ],
    'up_idle': pygame.image.load('./spidy_walk_up_idle.png')
}

MSG_SWITCH_WEAPON = 14
MSG_HEAL = 15
MSG_STRENGTH = 16
MSG_WEAPON_UPDATE = 17
MSG_TOGGLE_BOT = 21
WEAPON = ""


def client_player_would_collide(x, y):
    # Use player's lower body as collision box (waist to feet)
    # This matches how lava works and feels natural
    col_left   = x
    col_right  = x + PLAYER_WIDTH
    col_top    = y
    col_bottom = y + PLAYER_HEIGHT

    left_tile   = int((col_left   + MAP_HALF_WIDTH)  // TILE_SIZE)
    right_tile  = int((col_right  + MAP_HALF_WIDTH)  // TILE_SIZE)
    top_tile    = int((col_top    + MAP_HALF_HEIGHT) // TILE_SIZE)
    bottom_tile = int((col_bottom + MAP_HALF_HEIGHT) // TILE_SIZE)

    for tx in range(left_tile, right_tile + 1):
        for ty in range(top_tile, bottom_tile + 1):
            tile = TILE_DICT.get((tx, ty))
            if tile:
                _, _, _, walkable, _ch = tile
                if not walkable:
                    return True
    return False

class EnemyBulletClient:
    def __init__(self, bullet_id):
        self.bullet_id = bullet_id
        self.x = 0
        self.y = 0
        self.vx = 0
        self.vy = 0
        self.ttl = 0

    def update_from_server(self, x, y, vx, vy, ttl):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.ttl = ttl

    def draw(self, screen, cam_x, cam_y):
        screen_x = int(self.x - cam_x)
        screen_y = int(self.y - cam_y)
        pygame.draw.circle(screen, (255, 120, 40), (screen_x, screen_y), 4)

class EnemyClient:
    def __init__(self, enemy_id, enemy_type, men_frames, women_frames, dir):
        self.enemy_id = enemy_id
        self.x = 0
        self.y = 0
        self.hp = 100

        self.dir_code = dir
        self.direction = "down"

        self.timer = 0
        self.frame_index = 0
        self.animation_speed = 0.15

        self.frames = men_frames if enemy_type == 0 else women_frames
        self.is_follow = False

    def update(self, dt):
        if self.is_follow == False:
            if self.dir_code == 0:
                self.direction = "left"
            elif self.dir_code == 1:
                self.direction = "right"
            elif self.dir_code == 2:
                self.direction = "up"
            elif self.dir_code == 3:
                self.direction = "down"

            self.timer += dt
            if self.timer > self.animation_speed:
                self.timer = 0
                self.frame_index = (self.frame_index + 1) % len(self.frames[self.direction])

            return self.frames[self.direction][self.frame_index]

        else:
            pass

    def draw(self, screen, cam_x, cam_y, dt):
        screen_x = self.x - cam_x
        screen_y = self.y - cam_y
        screen.blit(self.update(dt), (screen_x, screen_y))


class OtherPlayer:
    def __init__(self, sprites, x=0, y=0):
        self.x = x
        self.y = y
        self.hp = 100

        self.sprites = sprites

        self.direction = "down"
        self.moving = False

        self.frame_index = 0
        self.anim_timer = 0
        self.anim_speed = 0.15
        #self.last_x = x
        #self.last_y = y


    def update_from_server(self, x, y, hp, dir_code):
        self.x = x
        self.y = y
        self.hp = hp

        # direction
        if dir_code == 0:
            self.direction = "down"
        elif dir_code == 1:
            self.direction = "up"
        elif dir_code == 2:
            self.direction = "left"
        elif dir_code == 3:
            self.direction = "right"

        self.moving = True


    def animate(self, dt):
        if not self.moving:
            self.frame_index = 0
            return

        self.anim_timer += dt

        if self.anim_timer > self.anim_speed:
            self.anim_timer = 0
            self.frame_index = (self.frame_index + 1) % len(self.sprites[self.direction])


    def get_sprite(self):
        if not self.moving:
            return self.sprites[f"{self.direction}_idle"]

        return self.sprites[self.direction][self.frame_index]


    def draw(self, screen, cam_x, cam_y, dt):
        self.animate(dt)
        sprite = self.get_sprite()
        screen_x = int(self.x - cam_x)
        screen_y = int(self.y - cam_y)
        screen.blit(sprite, (screen_x, screen_y))
        self.moving = False


class InputBox:
    def __init__(self, x, y, w, h, font, text=''):
        self.font = font
        self.rect = pygame.Rect(x, y, w, h)
        self.color = (100, 100, 100)
        self.text = text
        self.txt_surface = self.font.render(text, True, (255, 255, 255))
        self.active = False

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            self.active = self.rect.collidepoint(event.pos)
            self.color = (200, 200, 255) if self.active else (100, 100, 100)
        if event.type == pygame.KEYDOWN and self.active:
            if event.key == pygame.K_BACKSPACE:
                self.text = self.text[:-1]
            else:
                self.text += event.unicode
            self.txt_surface = self.font.render(self.text, True, (255, 255, 255))

    def draw(self, screen):
        pygame.draw.rect(screen, self.color, self.rect, 2)
        screen.blit(self.txt_surface, (self.rect.x + 5, self.rect.y + 5))



class Player:
    def __init__(self, sprites):
        super().__init__()
        self.x = None
        self.y = None
        self.hp = None

        self.sprites = sprites
        self.direction = 'down'
        self.frame_index = 0
        self.anim_timer = 0
        self.weapon_id = 9

    def update_sprite(self, intent, dt):
        if intent & UP:
            self.direction = 'up'
        elif intent & DOWN:
            self.direction = 'down'
        elif intent & LEFT:
            self.direction = 'left'
        elif intent & RIGHT:
            self.direction = 'right'

        if intent == 0:
            return self.sprites[f"{self.direction}_idle"]

        self.anim_timer += dt
        if self.anim_timer > 0.15:
            self.anim_timer = 0
            self.frame_index = (self.frame_index + 1) % len(self.sprites[self.direction])

        return self.sprites[self.direction][self.frame_index]


class GameClientProtocol(QuicConnectionProtocol):
    def __init__(self, token, carry_inputs, carry_seq, intent, last_intent, cam_x, cam_y, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.token = token

        self.client_id = None
        self.connected = False

        self.player = Player(SELF_SPRITES_LIST)
        self.players = {}

        self.bullets = {}

        self.image = pygame.image.load(IMAGE)
        self.rect = self.image.get_rect()
        self.rect.x = WIDTH//2 - 18
        self.rect.y = HEIGHT//2 - 28

        self.img_inventory = pygame.image.load(INVENTORY)
        self.img_knife1 = pygame.image.load(KNIFE)
        self.img_knife = pygame.transform.scale(self.img_knife1, (40, 40))
        self.img_gun1 = pygame.image.load(GUN)
        self.img_gun = pygame.transform.scale(self.img_gun1, (40, 40))
        self.img_heal1 = pygame.image.load(HEALTH)
        self.img_heal = pygame.transform.scale(self.img_heal1, (20, 30))
        self.img_stretch1 = pygame.image.load(STRENGTH)
        self.img_stretch = pygame.transform.scale(self.img_stretch1, (20, 30))
        self.img_shield1 = pygame.image.load(SHIELD)
        self.img_shield = pygame.transform.scale(self.img_shield1, (20, 30))
        self.img_bow1 = pygame.image.load(BOW)
        self.img_bow = pygame.transform.scale(self.img_bow1, (20, 30))

        self.inventory_slots = [0, 0, 0, 0, 0, 0, 0]

        self.local_shield_active = False
        self.local_shield_start_time = 0.0
        self.local_shield_duration = 5.0

        self.input_seq = carry_seq
        self.pending_inputs = carry_inputs

        self.control_stream_id = None
        self.input_stream_id = None

        self.recv_buffers = {}

        self.last_server_activity = time.monotonic()
        self.last_ping_sent = 0.0

        self.message_queue = deque()

        self.initialized = False

        self.last_lava_check = time.monotonic()
        self.local_damage_seq = 0
        self.last_server_damage_seq = 0
        self.pending_damage = []

        self.last_sent_input = last_intent
        self.current_intent = intent

        self.cam_x = cam_x
        self.cam_y = cam_y

    def quic_event_received(self, event):
        if isinstance(event, HandshakeCompleted):
            self.send_login_token()
            self.connected = True
            self.input_stream_id = self._quic.get_next_available_stream_id(True)

        elif isinstance(event, StreamDataReceived):
            if event.stream_id not in self.recv_buffers:
                self.recv_buffers[event.stream_id] = bytearray()

            self.recv_buffers[event.stream_id].extend(event.data)
            self._process_buffer(event.stream_id)

    def send_login_token(self):
        if isinstance(self.token, str):
            token_bytes = self.token.encode()
        else:
            token_bytes = self.token
        payload = struct.pack("!B", 9) + token_bytes

        ctrl_stream = self._quic.get_next_available_stream_id(False)

        final_packet = struct.pack("!H", len(payload)) + payload

        self._quic.send_stream_data(ctrl_stream, final_packet, end_stream=False)
        self.transmit()

    def _process_buffer(self, stream_id):
        """process all complete messages in the buffer"""
        buffer = self.recv_buffers[stream_id]
        while True:
            if len(buffer) < 2:
                return  # Not enough for length

            msg_len = struct.unpack("!H", buffer[:2])[0]

            if len(buffer) < 2 + msg_len:
                return  # Wait for full message

            payload = buffer[2:2 + msg_len]
            del buffer[:2 + msg_len]

            self.message_queue.append((payload, stream_id))

    def process_pending_messages(self):
        """call this from the game loop to precess network messages"""
        while self.message_queue:
            payload, stream_id = self.message_queue.popleft()
            self._handle_message(payload, stream_id)

    def send_heartbeat(self):
        if not self.connected or self.input_stream_id is None:
            return

        payload = struct.pack("!B", 5)  # msg_type 5 = ping
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.input_stream_id, packet, end_stream=False)
        self.transmit()

    def change_weapon(self):
        global WEAPON
        if WEAPON == "heal" and inventory[3] == 0:
            WEAPON = ""
            self.send_switch_weapon(6)
        elif WEAPON == "strength" and inventory[4] == 0:
            WEAPON = ""
            self.send_switch_weapon(6)
        elif WEAPON == "shield" and inventory[5] == 0:
            WEAPON = ""
            self.send_switch_weapon(6)


    def _handle_message(self, data, stream_id):
        self.last_server_activity = time.monotonic()
        msg_type = data[0]

        if msg_type == 1:
            raw_id, x, y, hp, direction, wid= struct.unpack("!16sfffBB", data[1:])
            client_id = uuid.UUID(bytes=raw_id)

            if client_id != self.client_id:
                if client_id not in self.players:
                    player = OtherPlayer(OTHER_SPRITES_LIST)
                    rect = self.image.get_rect()
                    self.players[client_id] = [player, rect]
                    # Update their relative screen coordinates so they actually move on screen!
                    if self.client_id in self.players:
                        self.players[client_id][1].x = self.players[self.client_id][0].x - int(x)
                        self.players[client_id][1].y = self.players[self.client_id][0].y - int(y)

                if client_id in self.players:
                    player = self.players[client_id][0]
                    self.players[client_id][0].weapon_id = wid
                    player.update_from_server(x, y, hp, direction)

        elif msg_type == 0:  # message after handshake
            raw_id, x, y, hp = struct.unpack("!16sfff", data[1:])
            client_id = uuid.UUID(bytes=raw_id)
            self.control_stream_id = stream_id
            if not self.client_id:
                self.client_id = client_id
            self.players[client_id] = [self.player, self.rect]
            self.players[client_id][0].x = x
            self.players[client_id][0].y = y
            self.players[client_id][0].hp = hp
            self.initialized = True

        elif msg_type == 3:  # a player disconnected
            raw_id = struct.unpack("!16s", data[1:])[0]
            client_id = uuid.UUID(bytes=raw_id)
            if client_id == self.client_id:
                return

            self.players.pop(client_id, None)

        elif msg_type == 2:  # players already online
            raw_id, x, y, hp, wid = struct.unpack("!16sfffB", data[1:])
            client_id = uuid.UUID(bytes=raw_id)
            if client_id != self.client_id:
                if client_id not in self.players:
                    player = OtherPlayer(OTHER_SPRITES_LIST, x, y)
                    rect = self.image.get_rect()
                    self.players[client_id] = [player, rect]
                    self.players[client_id][0].hp = hp
                    self.players[client_id][0].x = x
                    self.players[client_id][0].y = y
                    self.players[client_id][0].weapon_id = wid
                    self.players[client_id][1].x = self.players[self.client_id][0].x - int(x)
                    self.players[client_id][1].y = self.players[self.client_id][0].y - int(y)


        elif msg_type == 4:  # local movement update
            raw_id, x, y, last_seq = struct.unpack("!16sffH", data[1:])
            client_id = uuid.UUID(bytes=raw_id)
            if client_id == self.client_id:
                # Delete history the server has acknowledged
                kept_inputs = []
                for (seq, saved_intent) in self.pending_inputs:
                    if seq_newer(seq, last_seq):
                        kept_inputs.append((seq, saved_intent))
                self.pending_inputs = kept_inputs

                # Save smooth visual position
                smooth_x = self.players[self.client_id][0].x
                smooth_y = self.players[self.client_id][0].y

                # Snap to server truth
                self.players[self.client_id][0].x = x
                self.players[self.client_id][0].y = y

                # Replay the history we just saved in the game loop
                for (seq, saved_intent) in self.pending_inputs:
                    self._prediction(saved_intent, 1.0 / 60.0)

                # Hide stutter if within tolerance
                diff_x = abs(self.players[self.client_id][0].x - smooth_x)
                diff_y = abs(self.players[self.client_id][0].y - smooth_y)

                if diff_x < 10 and diff_y < 10:
                    self.players[self.client_id][0].x = smooth_x
                    self.players[self.client_id][0].y = smooth_y

        elif msg_type == 5: # new player joined
            raw_id, x, y, hp = struct.unpack("!16sfff", data[1:])
            client_id = uuid.UUID(bytes=raw_id)
            if client_id != self.client_id:
                if client_id not in self.players:
                    player = OtherPlayer(OTHER_SPRITES_LIST, x, y)
                    rect = self.image.get_rect()
                    self.players[client_id] = [player, rect]

                self.players[client_id][0].hp = hp
                self.players[client_id][0].x = x
                self.players[client_id][0].y = y
                self.players[client_id][1].x = self.players[self.client_id][0].x - int(x)
                self.players[client_id][1].y = self.players[self.client_id][0].y - int(y)

        elif msg_type == 6:
            pass

        elif msg_type == 7: # local hp change
            raw_id, hp, server_seq = struct.unpack("!16sfH", data[1:])
            cid = uuid.UUID(bytes=raw_id)
            if cid != self.client_id:
                return

            # authoritative snap
            self.last_server_damage_seq = server_seq
            self.player.hp = hp

            # if server healed us (respawn), clear predictions
            if hp == 100:
                self.pending_damage.clear()
                self.local_damage_seq = server_seq

            # discard confirmed predictions
            self.pending_damage = [
                seq for seq in self.pending_damage if seq_newer(seq, server_seq)
            ]

            # reapply unconfirmed predicted damage
            for _ in self.pending_damage:
                self.player.hp -= LAVA_DAMAGE

        elif msg_type == 8:
            raw_id, hp, server_seq = struct.unpack("!16sfH", data[1:])
            cid = uuid.UUID(bytes=raw_id)
            if cid != self.client_id:
                self.players[cid][0].hp = hp

        elif msg_type == 9:
            user_len, msg_len = struct.unpack("!II", data[1:9])

            user = str(data[9:9 + user_len].decode())

            index = 9 + user_len
            msg = str(data[index:index + msg_len].decode())

            MSG_HISTORY.append((f"{user}: ", msg))

            if len(MSG_HISTORY) > 40:
                MSG_HISTORY.pop(0)

            global IN_CHAT, UNREAD_MSGS
            if not IN_CHAT:
                UNREAD_MSGS += 1


        elif msg_type == 10:
            pid, ip, port, token = struct.unpack("!16s4si16s", data[1:])
            # Convert packed IP back to string
            ip_str = socket.inet_ntoa(ip)
            new_token = uuid.UUID(bytes=token).hex
            global RECONNECT_INFO
            RECONNECT_INFO = {
                "ip": ip_str,
                "port": port,
                "token": new_token,
                "carryover_inputs": self.pending_inputs,
                "carryover_seq": self.input_seq,
                "last_intent": self.last_sent_input,
                "intent": self.current_intent,
                "cam_x": self.cam_x,
                "cam_y": self.cam_y

            }
            # Mark as disconnected so send_intent/send_heartbeat don't
            # try to write to the now-closing QUIC stream and crash.
            self.connected = False
            self._quic.close()


        elif msg_type == 11:  # enemy update
            enemy_id, x, y, hp, direction = struct.unpack("!ffffI", data[1:])

            enemy_id = int(enemy_id)

            if enemy_id not in ENEMIES:
                enemy_type = 0 if enemy_id <= 13 else 1
                ENEMIES[enemy_id] = EnemyClient(enemy_id, enemy_type, WOMEN_SPRITES, MEN_SPRITES, direction)

            enemy = ENEMIES[enemy_id]
            enemy.x = x
            enemy.y = y
            enemy.hp = hp
            enemy.dir_code = direction % 4

        elif msg_type == 12:  # BULLET_SPAWN
            raw_id, x0, y0, vx, vy, ttl = struct.unpack("!16sfffff", data[1:])
            shooter = uuid.UUID(bytes=raw_id)
            self.bullets[shooter] = {"x": x0, "y": y0, "vx": vx, "vy": vy, "ttl": ttl}


        elif msg_type == 13:  # BULLET_DESPAWN
            raw_id = struct.unpack("!16s", data[1:])[0]
            shooter = uuid.UUID(bytes=raw_id)
            self.bullets.pop(shooter, None)

        elif msg_type == MSG_WEAPON_UPDATE:
            raw_id, wid = struct.unpack("!16sB", data[1:])
            cid = uuid.UUID(bytes=raw_id)
            if cid in self.players:
                self.players[cid][0].weapon_id = wid

        elif msg_type == 18:  # ENEMY_BULLET_SPAWN
            bullet_id, x, y, vx, vy, ttl = struct.unpack("!Ifffff", data[1:])

            if bullet_id not in ENEMY_BULLETS:
                ENEMY_BULLETS[bullet_id] = EnemyBulletClient(bullet_id)

            ENEMY_BULLETS[bullet_id].update_from_server(x, y, vx, vy, ttl)

        elif msg_type == 19:  # ENEMY_BULLET_DESPAWN
            bullet_id = struct.unpack("!I", data[1:])[0]
            ENEMY_BULLETS.pop(bullet_id, None)

        elif msg_type == 20:#inventory
            global inventory
            s0, s1, s2, s3, s4, s5, s6 = struct.unpack("!BBBBBBB", data[1:8])
            self.inventory_slots = [s0, s1, s2, s3, s4, s5, s6]
            inventory = self.inventory_slots
            self.change_weapon()

    async def connect_to_other_server(self, server_host, server_ip, token, server_port, pid):
        configuration = QuicConfiguration(
            is_client=True,
            alpn_protocols=["mmo"]  # Set label as mmo
        )
        # For self-signed certs → disable verification (LAN only!)
        configuration.verify_mode = ssl.CERT_REQUIRED
        configuration.load_verify_locations("ca.cert.pem")
        configuration.server_name = server_host

        protocol_factory = lambda *args, **kwargs: GameClientProtocol(token, *args, **kwargs)

        try:
            async with connect(
                    server_ip,
                    server_port,  # let os choose a free port
                    configuration=configuration,
                    create_protocol=protocol_factory,
                    stream_handler=None  # Optional: skips auto stream handling since we are custom
            ) as client:
                pid = uuid.UUID(bytes=pid)
                client.id = pid

                self._quic.close()

                while not client.connected:
                    await asyncio.sleep(0.01)

        except Exception as e:
            _log_obfuscated_error("connect_to_other_server", e)
    def send_intent(self, intent):
        if not self.initialized:
            return

        if self.client_id not in self.players:
            return

        if not self.connected or self.input_stream_id is None:
            return

        self.current_intent = intent

        payload = struct.pack("!BBH", 1, intent, self.input_seq)  # H stands for unsigned short
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.input_stream_id, packet, end_stream=False)
        self.transmit()

    def draw_chat_icon(self, screen) -> None:
        global UNREAD_MSGS, IN_CHAT

        # Don't draw the icon if the chat box is open
        if IN_CHAT:
            return

        # Icon dimensions and position (Bottom-Left)
        radius = 30
        cx = 40
        cy = HEIGHT - 40

        # 1. Draw the dark, semi-transparent base circle
        icon_surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(icon_surf, (0, 0, 0, 160), (radius, radius), radius)

        # 2. Draw a little white speech bubble inside
        bubble_rect = pygame.Rect(12, 16, 36, 20)
        pygame.draw.rect(icon_surf, (200, 200, 200), bubble_rect, border_radius=6)
        # The little tail of the speech bubble
        pygame.draw.polygon(icon_surf, (200, 200, 200), [(18, 35), (24, 43), (28, 35)])

        # Blit the base circle to the screen
        screen.blit(icon_surf, (cx - radius, cy - radius))

        # 3. Draw the Red Notification Badge
        if UNREAD_MSGS > 0:
            badge_radius = 12
            bx = cx + radius - 8  # Offset to Top-Right
            by = cy - radius + 8

            # Red circle
            pygame.draw.circle(screen, (255, 40, 40), (bx, by), badge_radius)

            # Text inside the badge
            font = pygame.font.SysFont(None, 20)

            # Cap the display at +40
            display_text = f"+{UNREAD_MSGS}" if UNREAD_MSGS <= 99 else "+99"

            txt_surf = font.render(display_text, True, (255, 255, 255))
            txt_rect = txt_surf.get_rect(center=(bx, by))
            screen.blit(txt_surf, txt_rect)

    def draw_minimap(self, screen):
        # Ensure the local player exists before drawing
        if self.client_id not in self.players:
            return

        local_player = self.players[self.client_id][0]

        # Minimap configuration
        minimap_size = 200
        # Position in the top right corner
        minimap_rect = pygame.Rect(WIDTH - minimap_size - 20, 20, minimap_size, minimap_size)

        # Create a semi-transparent surface for the minimap
        minimap_surface = pygame.Surface((minimap_size, minimap_size), pygame.SRCALPHA)
        minimap_surface.fill((0, 0, 0, 180))  # Black with opacity

        # Determine the radius of the world to display (e.g., 4000x4000 pixel area)
        world_view_size = 4000
        scale = minimap_size / world_view_size

        # Center of the minimap in world coordinates
        center_x = local_player.x
        center_y = local_player.y

        # --- Draw Terrain (Trees and Lava) ---
        # Calculate which tiles are currently within the minimap's radius
        left_tile = int((center_x - world_view_size / 2 + MAP_HALF_WIDTH) // TILE_SIZE)
        right_tile = int((center_x + world_view_size / 2 + MAP_HALF_WIDTH) // TILE_SIZE)
        top_tile = int((center_y - world_view_size / 2 + MAP_HALF_HEIGHT) // TILE_SIZE)
        bottom_tile = int((center_y + world_view_size / 2 + MAP_HALF_HEIGHT) // TILE_SIZE)

        for ty in range(top_tile, bottom_tile + 1):
            for tx in range(left_tile, right_tile + 1):
                tile = TILE_DICT.get((tx, ty))
                if tile:
                    _, world_x, world_y, walkable, ch = tile

                    # Determine color based on tile type
                    if ch == 'T':
                        color = (34, 139, 34)  # Forest Green for trees
                    elif ch in ['#', '←', '→', '↑', '↓', '↖', '↗', '↘', '↙', '⇦', '⇨', '⇧', '⇩']:
                        color = (255, 140, 0)  # Orange for lava
                    else:
                        continue  # Skip normal ground

                    # Calculate scaled position
                    mx = int((world_x - center_x) * scale + minimap_size / 2)
                    my = int((world_y - center_y) * scale + minimap_size / 2)
                    rect_size = max(1, int(TILE_SIZE * scale))

                    if 0 <= mx < minimap_size and 0 <= my < minimap_size:
                        pygame.draw.rect(minimap_surface, color, (mx, my, rect_size, rect_size))

        # --- Draw Other Players (Green) ---
        for pid, (player, _) in self.players.items():
            if pid == self.client_id:
                continue

            mx = int((player.x - center_x) * scale + minimap_size / 2)
            my = int((player.y - center_y) * scale + minimap_size / 2)

            if 0 <= mx < minimap_size and 0 <= my < minimap_size:
                pygame.draw.circle(minimap_surface, (0, 255, 0), (mx, my), 3)

        # --- Draw Enemies (Red) ---
        for enemy_id, enemy in ENEMIES.items():
            mx = int((enemy.x - center_x) * scale + minimap_size / 2)
            my = int((enemy.y - center_y) * scale + minimap_size / 2)

            if 0 <= mx < minimap_size and 0 <= my < minimap_size:
                pygame.draw.circle(minimap_surface, (255, 0, 0), (mx, my), 3)

        # --- Draw Local Player (Cyan) ---
        pygame.draw.circle(minimap_surface, (0, 255, 255), (minimap_size // 2, minimap_size // 2), 4)

        # --- Draw Minimap Border ---
        pygame.draw.rect(minimap_surface, (200, 200, 200), minimap_surface.get_rect(), 2)

        # --- Blit the minimap surface onto the main screen ---
        screen.blit(minimap_surface, minimap_rect.topleft)

        # --- Draw Player Coordinates ---
        client = self.players[self.client_id][0]

        # Format the strings to 1 decimal place cleanly
        x_str = f"x: {client.x:.1f}"
        y_str = f"y: {client.y:.1f}"

        fnt = pygame.font.SysFont("Ariel", 20)

        # Render the text surfaces separately
        x_text = fnt.render(x_str, True, pygame.Color("White"))
        y_text = fnt.render(y_str, True, pygame.Color("White"))

        # Fixed base position (Left align of the minimap)
        base_x = WIDTH - minimap_size - 20
        base_y = minimap_size + 22

        # Draw X at the base position
        screen.blit(x_text, (base_x, base_y))

        # Draw Y at a fixed offset of 110 pixels to the right.
        screen.blit(y_text, (base_x + 110, base_y))

    def draw(self, screen):
        if not self.initialized:
            return

        if self.client_id not in self.players:
            return

        local_player = self.players[self.client_id][0]

        # draw background using camera offset
        target_cam_x = local_player.x - (WIDTH // 2)
        target_cam_y = local_player.y - (HEIGHT // 2)

        if getattr(self, "current_intent", 0) & SPRINT:
            smoothing = 0.2
        else:
            smoothing = 0.1

        self.cam_x += (target_cam_x - self.cam_x) * smoothing
        self.cam_y += (target_cam_y - self.cam_y) * smoothing

        cam_x = self.cam_x
        cam_y = self.cam_y

        cam_x = max(-MAP_HALF_WIDTH, min(cam_x, MAP_HALF_WIDTH - WIDTH))
        cam_y = max(-MAP_HALF_HEIGHT, min(cam_y, MAP_HALF_HEIGHT - HEIGHT))

        world_left = cam_x
        world_right = cam_x + WIDTH
        world_top = cam_y
        world_bottom = cam_y + HEIGHT

        left = int(((world_left + MAP_HALF_WIDTH) // TILE_SIZE)) - 1
        right = int(((world_right + MAP_HALF_WIDTH) // TILE_SIZE)) + 1
        top = int(((world_top + MAP_HALF_HEIGHT) // TILE_SIZE)) - 1
        bottom = int(((world_bottom + MAP_HALF_HEIGHT) // TILE_SIZE)) + 1

        left = max(left, 0)
        right = min(right, 1919)
        top = max(top, 0)
        bottom = min(bottom, 1079)

        trees_to_draw = []

        for ty in range(top, bottom + 1):
            for tx in range(left, right + 1):
                tile = TILE_DICT.get((tx, ty))
                if tile is None:
                    continue
                image, world_x, world_y, _, _ch = tile

                screen_x = world_x - cam_x
                screen_y = world_y - cam_y

                if _ch == 'T':
                    # Draw a regular ground tile beneath the tree to prevent a black void
                    if '.' in TILE_DEFS:
                        ground_img, _ = TILE_DEFS['.']
                        screen.blit(ground_img, (screen_x, screen_y))

                    # Store the tree to be drawn later (on top of players)
                    trees_to_draw.append((image, screen_x, screen_y))
                else:
                    screen.blit(image, (screen_x, screen_y))

        max_hp = 100

        for pid, (player, _) in self.players.items():
            if pid != self.client_id:
                #player.animate(1 / 60)

                screen_x = player.x - cam_x
                screen_y = player.y - cam_y
                player.draw(screen, cam_x, cam_y, FIXED_DT)
                self.draw_weapon_by_id(screen, screen_x, screen_y, getattr(player, "weapon_id", 9))

        for enemy in ENEMIES.values():
            enemy.draw(screen, cam_x, cam_y, FIXED_DT)

        for bullet in ENEMY_BULLETS.values():
            bullet.draw(screen, cam_x, cam_y)

        for pid, (player, _) in self.players.items():
            if pid == self.client_id:
                continue

            ratio = max(0, player.hp) / max_hp

            screen_x = player.x - cam_x
            screen_y = player.y - cam_y

            bar_x = screen_x + PLAYER_WIDTH // 2 - HP_BAR_WIDTH // 2
            bar_y = screen_y - HP_BAR_OFFSET_Y

            pygame.draw.rect(
                screen,
                (255, 0, 0),
                (bar_x, bar_y, HP_BAR_WIDTH, HP_BAR_HEIGHT)
            )
            pygame.draw.rect(
                screen,
                (0, 255, 0),
                (bar_x, bar_y, HP_BAR_WIDTH * ratio, HP_BAR_HEIGHT)
            )

        for tree_img, tx, ty in trees_to_draw:
            screen.blit(tree_img, (tx, ty))

        for b in self.bullets.values():
            screen_x = b["x"] - cam_x + 15
            screen_y = b["y"] - cam_y + 15
            pygame.draw.circle(screen, (255, 255, 0), (int(screen_x), int(screen_y)), 4)

        item = self.players[self.client_id]
        screen_x = item[0].x - cam_x
        screen_y = item[0].y - cam_y
        sprite = self.player.update_sprite(self.current_intent, FIXED_DT)
        screen.blit(sprite, (screen_x, screen_y))
        self.change_sprite_weapon(screen_x, screen_y, screen)

        ratio = max(0, self.player.hp) / max_hp
        pygame.draw.rect(screen, (255, 0, 0), (20, 40, 200, 10))
        pygame.draw.rect(screen, (0, 255, 0), (20, 40, 200 * ratio, 10))

        screen.blit(self.img_inventory, (300, 300))

        slots_pos = {
            0: (385, 570),
            1: (430, 570),
            2: (480, 580),
            3: (523, 580),
            4: (565, 580),
            5: (607, 580),
        }

        for i, wid in enumerate(self.inventory_slots[:6]):
            x, y = slots_pos[i]

            if wid == 1:
                screen.blit(self.img_gun, (x, y))
            elif wid == 2:
                screen.blit(self.img_knife, (x, y))
            elif wid == 3:
                screen.blit(self.img_bow, (x, y))
            elif wid == 4:
                screen.blit(self.img_heal, (x, y))
            elif wid == 5:
                screen.blit(self.img_stretch, (x, y))
            elif wid == 6:
                screen.blit(self.img_shield, (x, y))

        self.draw_chat_icon(screen)

        self.draw_minimap(screen)


    def draw_weapon_by_id(self, screen, x, y, wid: int):
        if wid == 1:  # pistol
            screen.blit(self.img_gun, (x, y + 5))
        elif wid == 2:  # knife
            screen.blit(self.img_knife, (x, y + 5))
        elif wid == 3: # bow
            screen.blit(self.img_bow, (x, y + 5))
        elif wid == 4:  # heal
            img = pygame.transform.scale(self.img_heal, (20, 20))
            screen.blit(img, (x, y + 15))
        elif wid == 5:  # strength
            img = pygame.transform.scale(self.img_stretch, (20, 20))
            screen.blit(img, (x, y + 15))
        elif wid == 6:
            img = pygame.transform.scale(self.img_shield, (20, 20))
            screen.blit(img, (x, y + 15))
        else:
            pass

    def send_disconnect(self):
        if self.client_id not in self.players:
            return

        if self.control_stream_id is not None:
            self.connected = False
            payload = struct.pack("!BB", 0, 0)
            packet = struct.pack("!H", len(payload)) + payload
            self._quic.send_stream_data(self.control_stream_id, packet, end_stream=True)
            self.transmit()

    def update_bullets(self, dt: float):
        dead = []
        for shooter, b in self.bullets.items():
            b["x"] += b["vx"] * dt
            b["y"] += b["vy"] * dt
            b["ttl"] -= dt
            if b["ttl"] <= 0:
                dead.append(shooter)
        for s in dead:
            self.bullets.pop(s, None)

    def update_enemy_bullets(self, dt: float):
        dead = []
        for bullet_id, b in ENEMY_BULLETS.items():
            b.x += b.vx * dt
            b.y += b.vy * dt
            b.ttl -= dt

            if b.ttl <= 0:
                dead.append(bullet_id)

        for bullet_id in dead:
            ENEMY_BULLETS.pop(bullet_id, None)

    def _prediction(self, intent, dt):
        if self.client_id not in self.players:
            return

        dir_x = 0
        dir_y = 0

        if intent & LEFT:
            dir_x -= 1
        if intent & RIGHT:
            dir_x += 1
        if intent & UP:
            dir_y -= 1
        if intent & DOWN:
            dir_y += 1

        length = math.hypot(dir_x , dir_y)
        if length != 0:
            dir_x /= length
            dir_y /= length

        speed = SPEED
        if intent & SPRINT and not intent & CROUCH:
            speed = SPRINT_SPEED
        elif intent & CROUCH and not intent & SPRINT:
            speed = CROUCH_SPEED

        if dir_x != 0 or dir_y != 0:
            dx = dir_x * speed * dt
            dy = dir_y * speed * dt

            if dx != 0:
                self.collisions(dx, 0)
            if dy != 0:
                self.collisions(0, dy)

    def collisions(self, dx, dy):
        local_player = self.players[self.client_id][0]
        tolerance = 6.0

        entities = []
        for pid, (client, _) in self.players.items():
            if pid == self.client_id:
                continue
            entities.append((client.x, client.y))

        allowed_dx = dx
        allowed_dy = dy

        # התנגשות מול שחקנים אחרים
        for ex, ey in entities:
            if abs(ex - local_player.x) > (PLAYER_WIDTH + abs(dx)):
                continue
            if abs(ey - local_player.y) > (PLAYER_HEIGHT + abs(dy)):
                continue

            inline_x = abs(local_player.x - ex) < (PLAYER_WIDTH - tolerance)
            inline_y = abs(local_player.y - ey) < (PLAYER_HEIGHT - tolerance)

            if dx != 0 and inline_y:
                if dx * (ex - local_player.x) > 0:
                    if dx > 0:
                        test_x = local_player.x + allowed_dx
                        if local_player.x <= ex and test_x > ex - (PLAYER_WIDTH - tolerance):
                            allowed_dx = max(
                                0.0,
                                min(allowed_dx, ex - local_player.x - (PLAYER_WIDTH - tolerance))
                            )
                    elif dx < 0:
                        test_x = local_player.x + allowed_dx
                        if local_player.x >= ex and test_x < ex + (PLAYER_WIDTH - tolerance):
                            allowed_dx = min(
                                0.0,
                                max(allowed_dx, ex + (PLAYER_WIDTH - tolerance) - local_player.x)
                            )

            if dy != 0 and inline_x:
                if dy * (ey - local_player.y) > 0:
                    if dy > 0:
                        test_y = local_player.y + allowed_dy
                        if local_player.y <= ey and test_y > ey - (PLAYER_HEIGHT - tolerance):
                            allowed_dy = max(
                                0.0,
                                min(allowed_dy, ey - local_player.y - (PLAYER_HEIGHT - tolerance))
                            )
                    elif dy < 0:
                        test_y = local_player.y + allowed_dy
                        if local_player.y >= ey and test_y < ey + (PLAYER_HEIGHT - tolerance):
                            allowed_dy = min(
                                0.0,
                                max(allowed_dy, ey + (PLAYER_HEIGHT - tolerance) - local_player.y)
                            )

        # התנגשות מול עצים / tiles חוסמים
        if allowed_dx != 0:
            test_x = local_player.x + allowed_dx
            if not client_player_would_collide(test_x, local_player.y):
                local_player.x = test_x

        if allowed_dy != 0:
            test_y = local_player.y + allowed_dy
            if not client_player_would_collide(local_player.x, test_y):
                local_player.y = test_y

        local_player.x = max(-MAP_HALF_WIDTH, min(local_player.x, MAP_HALF_WIDTH - PLAYER_WIDTH))
        local_player.y = max(-MAP_HALF_HEIGHT, min(local_player.y, MAP_HALF_HEIGHT - PLAYER_HEIGHT))

    def convert_images(self):
        self.image = self.image.convert_alpha()
        for ch, (img_path, walkable) in TILE_DEFS.items():
            img = pygame.image.load(img_path)
            if img_path == "tree.png":
                TILE_DEFS[ch] = (img.convert_alpha(), walkable)
            else:
                TILE_DEFS[ch] = (img.convert(), walkable)

    def is_in_lava(self):
        if not self.initialized or self.player.x is None or self.player.y is None:
            return False
        tx = int((self.player.x + (PLAYER_WIDTH - 18) + MAP_HALF_WIDTH) // TILE_SIZE)
        ty = int((self.player.y + (PLAYER_HEIGHT - 8) + MAP_HALF_HEIGHT) // TILE_SIZE)
        tile = TILE_DICT.get((tx, ty))
        if tile is None:
            return False
        _, _, _, _, ch = tile
        return ch == '#'

    def predict_lava_if_needed(self):
        # only predict if:
        # 1. we are standing in lava
        # 2. we have NO unconfirmed damage predicted
        if self.local_shield_active:
            if time.monotonic() - self.local_shield_start_time <= self.local_shield_duration:
                return
            else:
                self.local_shield_active = False

        if not self.is_in_lava():
            return

        if self.pending_damage:
            return  # already predicted one, wait for server

        # predict exactly ONE future server damage
        self.local_damage_seq = (self.local_damage_seq + 1) & 0xFFFF
        self.pending_damage.append(self.local_damage_seq)
        self.player.hp -= LAVA_DAMAGE

    def send_message(self, message):
        if self.client_id not in self.players:
            return

        message = message.encode()
        payload = struct.pack("!BI", 2, len(message)) + message
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self._quic.get_next_available_stream_id(), packet, end_stream=True)
        self.transmit()


    def send_switch_weapon(self, slot_index: int):
        if not self.initialized:
            return
        if not self.connected or self.input_stream_id is None:
            return

        payload = struct.pack("!BB", MSG_SWITCH_WEAPON, slot_index)
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.input_stream_id, packet, end_stream=False)
        self.transmit()

    def heal(self):
        if not self.initialized:
            return
        if not self.connected or self.input_stream_id is None:
            return

        payload = struct.pack("!B", MSG_HEAL)
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.input_stream_id, packet, end_stream=False)
        self.transmit()

    def strength(self):
        if not self.initialized:
            return
        if not self.connected or self.input_stream_id is None:
            return

        payload = struct.pack("!B", MSG_STRENGTH)
        packet = struct.pack("!H", len(payload)) + payload
        self._quic.send_stream_data(self.input_stream_id, packet, end_stream=False)
        self.transmit()

    def change_sprite_weapon(self, x, y, screen):
        if WEAPON=="gun":
            screen.blit(self.img_gun, (x, y+5))
        if WEAPON=="knife":
            screen.blit(self.img_knife, (x, y+5))
        if WEAPON=="heal":
            img_heal = pygame.transform.scale(self.img_heal, (20, 20))
            screen.blit(img_heal, (x, y+15))
        if WEAPON=="strength":
            img_strength = pygame.transform.scale(self.img_stretch, (20, 20))
            screen.blit(img_strength, (x, y+15))
        if WEAPON=="shield":
            img_shield = pygame.transform.scale(self.img_shield, (20, 20))
            screen.blit(img_shield, (x, y+15))
        if WEAPON == "bow":
            img_bow = pygame.transform.scale(self.img_bow, (20, 20))
            screen.blit(img_bow, (x, y+15))

# ==================
# CHAT
# ==================
def wrap_text(text, max_w, font, indent=0):
    """
    Takes a long string, checks its rendered width, and splits it
    into a list of shorter strings that fit inside max_width.
    """
    words = text.split(' ')
    lines, current_line, current_limit = [], "", max_w - indent
    for word in words:
        if font.size(current_line + word + " ")[0] < current_limit:
            current_line += word + " "

        elif font.size(word)[0] > current_limit:
            # If we have text currently pending, push it to a new line first
            if current_line:
                lines.append(current_line)
                current_line = ""

            # Now split the long word character by character
            for char in word:
                # If adding the next char exceeds the width...
                if font.size(current_line + char)[0] > current_limit:
                    lines.append(current_line)  # Push current part
                    current_line = char  # Start new part with char
                else:
                    current_line += char

            current_line += " "  # Add the space after the word finishes
            current_limit = max_w - indent  # Reset limit for subsequent lines

        else:
            lines.append(current_line)
            current_line = word + " "
            current_limit = max_w
    if current_line: lines.append(current_line)
    return lines


async def chat_processor(client):
    global MSG_HISTORY, IN_CHAT, CHAT_EVENTS, CHAT_STATE, RECONNECT_INFO
    # Task State
    max_msg_limit = 40
    max_msg_length = 256
    last_interaction = time.monotonic()
    task_active = True

    while task_active:
        if RECONNECT_INFO:
            break
        current_time = time.monotonic()

        # Timeout Check (Closes after 30 seconds of no interaction)
        if current_time - last_interaction > 30:
            break

        # Handle Inputs (Non-blocking, pulling from the shared list)
        while CHAT_EVENTS:
            event_ = CHAT_EVENTS.pop(0)
            last_interaction = current_time

            if event_.key == pygame.K_ESCAPE:
                task_active = False  # Kill the task entirely
                break

            elif event_.key == pygame.K_SLASH and not CHAT_STATE["focused"]:
                CHAT_STATE["focused"] = True  # Second slash focuses the input bar

            elif not CHAT_STATE["focused"]:
                # Scroll history up/down
                if event_.key == pygame.K_UP:
                    CHAT_STATE["scroll_offset"] = max(0, min(CHAT_STATE["scroll_offset"] + 1, len(MSG_HISTORY) - 8))
                elif event_.key == pygame.K_DOWN:
                    CHAT_STATE["scroll_offset"] = max(0, CHAT_STATE["scroll_offset"] - 1)

            elif CHAT_STATE["focused"]:
                # Typing logic
                if event_.key == pygame.K_RETURN:
                    if CHAT_STATE["current_input"].strip():
                        MSG_HISTORY.append(("You: ", CHAT_STATE["current_input"]))
                        client.send_message(CHAT_STATE["current_input"])
                        # Enforce 40 line capacity
                        if len(MSG_HISTORY) > max_msg_limit:
                            MSG_HISTORY.pop(0)

                    CHAT_STATE["current_input"] = ""
                    CHAT_STATE["scroll_offset"] = 0
                    CHAT_STATE["focused"] = False

                elif event_.key == pygame.K_BACKSPACE:
                    CHAT_STATE["current_input"] = CHAT_STATE["current_input"][:-1]

                elif not event_.key == pygame.K_SLASH:  # Ignore the activation slash
                    if event_.type == pygame.KEYDOWN:
                        if event_.unicode.isprintable():
                            if event_.unicode != "":
                                if len(CHAT_STATE["current_input"]) <= max_msg_length:
                                    CHAT_STATE["current_input"] += event_.unicode

        await asyncio.sleep(0.01)  # Yield control

    IN_CHAT = False
    CHAT_STATE["focused"] = False
    CHAT_STATE["current_input"] = ""
    CHAT_STATE["scroll_offset"] = 0


async def load_chat_box(screen, current_input, focused, scroll_offset):
    global MSG_HISTORY

    current_time = time.monotonic()

    # Set up the font (None uses the default pygame font, size 32)
    font = pygame.font.Font(None, 20)
    font_bold = pygame.font.Font(None, 20)
    font_bold.set_bold(True)  # Make this one bold

    # UI Dimensions & Limits
    screen_w, screen_h = screen.get_size()
    box_width = 450
    base_height = 200
    x_pos = 20
    padding = 10
    line_h = 25

    # Calculate UI layout (Expanding input box)
    max_text_w = box_width - (padding * 2)
    input_lines = wrap_text(f"> {current_input}", max_text_w, font)
    input_line_count = max(1, min(3, len(input_lines)))  # Expands up to 3 lines

    input_area_height = (input_line_count * line_h) + padding
    total_height = base_height + input_area_height
    y_pos = screen_h - total_height - 20  # Anchor to bottom left
    input_start_y = y_pos + base_height

     # Draw Background
    chat_bg = pygame.Surface((box_width, total_height), pygame.SRCALPHA)
    chat_bg.fill((0, 0, 0, 160))
    if focused:
        pygame.draw.rect(chat_bg, (0, 0, 0, 200), (0, base_height, box_width, input_area_height))
    screen.blit(chat_bg, (x_pos, y_pos))

    # Process and Wrap History
    render_queue = []
    for user, text in MSG_HISTORY:
        u_surf = font_bold.render(f"{user}: ", True, (255, 255, 100))
        wrapped_msg = wrap_text(text, max_text_w, font, indent=u_surf.get_width())

        for i, line in enumerate(wrapped_msg):
            if i == 0:
                render_queue.append({"type": "mixed", "user": u_surf, "text": line})
            else:
                render_queue.append({"type": "normal", "text": line})

    # Apply scrolling limits
    max_scroll = max(0, len(render_queue) - (base_height // line_h))
    scroll_offset = min(scroll_offset, max_scroll)

    # Draw History (Bottom-up)
    draw_y = input_start_y - line_h
    start_idx = len(render_queue) - 1 - scroll_offset

    for i in range(start_idx, -1, -1):
        if draw_y < y_pos + padding: break  # Hit the top of the box

        item = render_queue[i]
        if item["type"] == "mixed":
            screen.blit(item["user"], (x_pos + padding, draw_y))
            t_surf = font.render(item["text"], True, (255, 255, 255))
            screen.blit(t_surf, (x_pos + padding + item["user"].get_width(), draw_y))
        else:
            t_surf = font.render(item["text"], True, (255, 255, 255))
            screen.blit(t_surf, (x_pos + padding, draw_y))
        draw_y -= line_h

    # Draw Input Bar & Flashing Cursor
    pygame.draw.line(screen, (255, 255, 255, 100), (x_pos, input_start_y), (x_pos + box_width, input_start_y))

    inp_y = input_start_y + padding
    visible_input_lines = input_lines[-3:]  # Only show last 3 lines

    for line in visible_input_lines:
        color = (200, 255, 200) if focused else (150, 150, 150)
        screen.blit(font.render(line, True, color), (x_pos + padding, inp_y))

        # Flashing indicator on the very last line
        if focused and line == visible_input_lines[-1]:
            if int(current_time * 2) % 2 == 0:
                cursor_x = x_pos + padding + font.size(line)[0]
                pygame.draw.line(screen, (255, 255, 255), (cursor_x, inp_y + 2), (cursor_x, inp_y + line_h - 4), 2)
        inp_y += line_h



# ==================
# LOGIN / SIGNUP
# ==================
async def login_process(screen, clock, font, user_box, pass_box):
    msg = ""

    while True:
        screen.fill((20, 22, 28))

        # Draw Text
        screen.blit(font.render("Username:", True, (200, 200, 200)), (150, 160))
        screen.blit(font.render("Password:", True, (200, 200, 200)), (150, 230))
        screen.blit(font.render(msg, True, (255, 100, 100)), (280, 350))

        # Draw Buttons
        mx, my = pygame.mouse.get_pos()
        login_btn = pygame.Rect(280, 290, 90, 40)
        signup_btn = pygame.Rect(390, 290, 90, 40)

        pygame.draw.rect(screen, (0, 150, 0), login_btn)
        pygame.draw.rect(screen, (0, 0, 150), signup_btn)

        screen.blit(font.render("Login", True, (255, 255, 255)), (295, 300))
        screen.blit(font.render("Sign Up", True, (255, 255, 255)), (395, 300))

        user_box.draw(screen)
        pass_box.draw(screen)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return None

            user_box.handle_event(event)
            pass_box.handle_event(event)

            if event.type == pygame.MOUSEBUTTONDOWN:
                if login_btn.collidepoint((mx, my)):
                    res = await send_auth(user_box.text, pass_box.text, "login")
                    if res and res["success"]:
                        return res
                    else:
                        msg = "Login Failed"

                if signup_btn.collidepoint((mx, my)):
                    res = await send_auth(user_box.text, pass_box.text, "signup")
                    if res and res["success"]:
                        return res
                    else:
                        msg = (res or {}).get("msg", "Signup Failed")

        pygame.display.flip()
        clock.tick(30)


async def show_login_window():
    pygame.init()
    screen = pygame.display.set_mode((760, 460))
    pygame.display.set_caption("Cyber Login")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 32)

    user_box = InputBox(280, 150, 200, 40, font)
    pass_box = InputBox(280, 220, 200, 40, font)

    res = await login_process(screen, clock, font, user_box, pass_box)
    return res


# --- NETWORK HELPER FOR LOGIN ---
async def send_auth(u, p, mode):
    class AuthProto(QuicConnectionProtocol):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.data = asyncio.Future()

        def quic_event_received(self, event):
            if isinstance(event, StreamDataReceived):
                self.data.set_result(json.loads(event.data.decode()))

    config = QuicConfiguration(
        is_client=True,
        alpn_protocols=["manager-proto"]  # Set label as mmo
    )
    # For self-signed certs → disable verification (LAN only!)
    config.verify_mode = ssl.CERT_REQUIRED
    config.load_verify_locations("ca.cert.pem")
    config.server_name = MANAGER_HOST

    try:
        async with connect(MANAGER_IP, MANAGER_PORT, configuration=config, create_protocol=AuthProto) as client:
            # FIX 1: Added + "\n" so the Manager's new stream reader processes it instantly
            msg = json.dumps({"type": "AUTH_REQUEST", "username": u, "password": p, "mode": mode}) + "\n"

            client._quic.send_stream_data(client._quic.get_next_available_stream_id(), msg.encode(), end_stream=True)

            # FIX 2: Force the packet to actually push over the network!
            client.transmit()

            return await client.data
    except Exception as e:
        _log_obfuscated_error("send_auth", e)
        return None


# ===================
# GAME HELPERS
# ===================
def seq_newer(a, b):
    return ((a - b) & (SEQ_MAX - 1)) < SEQ_HALF


async def load_tile_map(path: str):
    tile_dict = {}

    with open(path, "r", encoding="utf-8") as f:
        for ty, line in enumerate(f):
            for tx, ch in enumerate(line.strip("\n")):
                if ch not in TILE_DEFS:
                    continue

                image, walkable = TILE_DEFS[ch]

                world_x = tx * TILE_SIZE - MAP_HALF_WIDTH
                world_y = ty * TILE_SIZE - MAP_HALF_HEIGHT

                tile_dict[(tx, ty)] = (image, world_x, world_y, walkable, ch)

    return tile_dict


async def display_fps(screen, clock):
    fnt = pygame.font.SysFont("Italian", 20)
    text_to_show = fnt.render(str(int(clock.get_fps())), 0, pygame.Color("Green"))
    screen.blit(text_to_show, (0, 0))


async def seamless_reconnect(info):
    global CURRENT_CLIENT, MANAGER_HOST
    old_client = CURRENT_CLIENT

    configuration = QuicConfiguration(is_client=True, alpn_protocols=["mmo"])
    configuration.verify_mode = ssl.CERT_REQUIRED
    configuration.load_verify_locations("ca.cert.pem")
    configuration.server_name = MANAGER_HOST

    protocol_factory = lambda *args, **kwargs: GameClientProtocol(
        info["token"],
        info.get("carryover_inputs", []),
        info.get("carryover_seq", getattr(old_client, "input_seq", 0)),
        old_client.current_intent,
        old_client.last_sent_input,
        old_client.cam_x,
        old_client.cam_y,
        *args, **kwargs
    )

    try:
        # Connect asynchronously (Main game loop keeps running!)
        manager = connect(
            info["ip"], info["port"],
            configuration=configuration,
            create_protocol=protocol_factory,
            stream_handler=None
        )
        # __aenter__() waits for the handshake to finish.
        # The split second this returns, we are officially connected to Server B.
        new_client = await manager.__aenter__()

        # DO NOT WAIT FOR AN INIT PACKET! Inherit everything from old_client immediately.
        new_client.players = old_client.players
        new_client.bullets = old_client.bullets
        new_client.just_reconnected = True

        # Trick the game loop into knowing this client is already fully set up
        new_client.initialized = getattr(old_client, "initialized", True)
        new_client._images_converted = getattr(old_client, "_images_converted", True)

        # Keep manager alive so Python's garbage collector doesn't drop the network task
        new_client._manager = manager

        # HOT SWAP (Takes 0.0001 seconds)
        CURRENT_CLIENT = new_client

        # SAFELY KILL THE OLD CONNECTION NOW THAT THE SWAP IS DONE
        try:
            old_client.send_disconnect()
        except:
            pass

        if hasattr(old_client, '_quic') and old_client._quic:
            old_client._quic.close()

    except Exception as e:
        _log_obfuscated_error("seamless_reconnect", e)


async def game_loop():
    global TILE_DICT, WEAPON, IN_CHAT, CHAT_EVENTS, CHAT_STATE, UNREAD_MSGS, FIXED_DT, RECONNECT_INFO, LOADED_WORLD, BOT_MODE_ACTIVE, CURRENT_CLIENT

    IN_CHAT = False
    CHAT_EVENTS.clear()
    CHAT_STATE["focused"] = False
    CHAT_STATE["current_input"] = ""
    CHAT_STATE["scroll_offset"] = 0

    running = True
    tick_accumulator = 0.0

    while running:
        if RECONNECT_INFO:
            info = RECONNECT_INFO.copy()
            RECONNECT_INFO = None
            asyncio.create_task(seamless_reconnect(info))

        client = CURRENT_CLIENT
        if not client:
            await asyncio.sleep(0.01)
            continue

        if not LOADED_WORLD:
            pygame.init()

            width, height = 1200, 700
            screen = pygame.display.set_mode((width, height))
            clock = pygame.time.Clock()
            pygame.display.set_caption("MMO Game")

            client.convert_images()
            TILE_DICT = await load_tile_map(MAP_PATH)

            LOADED_WORLD = {"screen": screen, "clock": clock}

        screen = LOADED_WORLD["screen"]
        clock = LOADED_WORLD["clock"]

        dt = clock.get_time() / 1000.0
        now = time.monotonic()
        intent = 0

        # Prevent spiral of death if the client freezes for a long time
        if dt > 0.25:
            dt = 0.25

        tick_accumulator += dt

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1:
                    intent |= SHOOT
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_1:
                    if WEAPON != "gun":
                        WEAPON = "gun"
                        client.send_switch_weapon(0)
                if event.key == pygame.K_b:
                    BOT_MODE_ACTIVE = not BOT_MODE_ACTIVE

                    payload = struct.pack(
                        '!B',
                        MSG_TOGGLE_BOT
                    )

                    packet = struct.pack('!H', len(payload)) + payload

                    try:
                        client._quic.send_stream_data(client.input_stream_id, packet, end_stream=False)
                    except Exception as e:
                        _log_obfuscated_error("game_loop.send_toggle_bot", e)
                        pass
                elif event.key == pygame.K_2:
                    if WEAPON != "knife":
                        WEAPON = "knife"
                        client.send_switch_weapon(1)
                elif event.key == pygame.K_3:
                    if inventory[2] != 0 and WEAPON != "bow":
                        WEAPON = "bow"
                        client.send_switch_weapon(2)
                    elif WEAPON != "":
                        WEAPON = ""
                        client.send_switch_weapon(6)
                elif event.key == pygame.K_9:
                    WEAPON = ""
                    client.send_switch_weapon(6)
                elif event.key == pygame.K_4:
                    if inventory[3] != 0 and WEAPON != "heal":
                        WEAPON = "heal"
                        client.send_switch_weapon(3)
                    elif WEAPON != "":
                        WEAPON = ""
                        client.send_switch_weapon(6)
                elif event.key == pygame.K_5:
                    if inventory[4] != 0 and WEAPON != "strength":
                        WEAPON = "strength"
                        client.send_switch_weapon(4)
                    elif WEAPON != "":
                        WEAPON = ""
                        client.send_switch_weapon(6)
                elif event.key == pygame.K_6:
                    if inventory[5] != 0 and WEAPON != "shield":
                        WEAPON = "shield"
                        client.send_switch_weapon(5)
                    elif WEAPON != "":
                        WEAPON = ""
                        client.send_switch_weapon(6)

                if event.key == pygame.K_SLASH and not IN_CHAT:
                    IN_CHAT = True
                    UNREAD_MSGS = 0
                    CHAT_EVENTS.clear()
                    asyncio.create_task(chat_processor(client))

                elif IN_CHAT:
                    CHAT_EVENTS.append(event)

        # ==========================================
        # CAPTURE OLD POSITION BEFORE NETWORK UPDATE
        # ==========================================
        if client.client_id in client.players:
            old_x = client.players[client.client_id][0].x
            old_y = client.players[client.client_id][0].y
        else:
            old_x = 0
            old_y = 0

        client.process_pending_messages()

        # ==========================================
        # APPLY LOCAL ANIMATION FIX IF BOT IS ACTIVE
        # ==========================================
        if BOT_MODE_ACTIVE and client.client_id in client.players:
            new_x = client.players[client.client_id][0].x
            new_y = client.players[client.client_id][0].y

            inferred_intent = client.current_intent
            if new_x != old_x or new_y != old_y:
                if new_y < old_y:
                    inferred_intent = UP
                elif new_y > old_y:
                    inferred_intent = DOWN
                elif new_x < old_x:
                    inferred_intent = LEFT
                elif new_x > old_x:
                    inferred_intent = RIGHT
            else:
                inferred_intent = 0

            client.current_intent = inferred_intent
            intent = inferred_intent

        client.predict_lava_if_needed()

        client.update_bullets(dt)
        client.update_enemy_bullets(dt)

        keys = pygame.key.get_pressed()
        mods = pygame.key.get_mods()

        # Disable WASD input if the bot is driving!
        if not CHAT_STATE["focused"] and not BOT_MODE_ACTIVE:
            if keys[pygame.K_w]:
                intent |= UP
            if keys[pygame.K_a]:
                intent |= LEFT
            if keys[pygame.K_s]:
                intent |= DOWN
            if keys[pygame.K_d]:
                intent |= RIGHT

            if mods & pygame.KMOD_CTRL:
                intent |= SPRINT

            if mods & pygame.KMOD_SHIFT:
                intent |= CROUCH

        elif CHAT_STATE["focused"]:
            intent = 0

        # Only check the direction bits (if at least one is on, continue)
        move_part = intent & (UP | LEFT | DOWN | RIGHT | SPRINT | CROUCH)
        shoot_part = intent & SHOOT

        if move_part & (UP | LEFT | DOWN | RIGHT):
            move_part = move_part & MOVE_MASK
        else:
            move_part = 0

        intent = move_part | shoot_part

        if getattr(client, "just_reconnected", False):
            # Force a resync with REAL keyboard state
            client.last_sent_input = -1  # invalidate old state
            client.current_intent = intent

            if client.initialized:
                client.send_intent(intent)
                client.last_sent_input = intent

            client.just_reconnected = False

        if now - client.last_ping_sent >= PING_INTERVAL:
            if client.initialized:
                client.send_heartbeat()
                client.last_ping_sent = now

        if now - client.last_server_activity > SERVER_TIMEOUT:
            client.connected = False
            pygame.quit()
            sys.exit(0)

        while tick_accumulator >= FIXED_DT:
            if not BOT_MODE_ACTIVE:
                client.input_seq = (client.input_seq + 1) % 65536
                client.pending_inputs.append((client.input_seq, intent))

                # ONLY send to the server if the state actually changed
                if intent != client.last_sent_input:
                    if client.initialized:
                        client.send_intent(intent)
                        client.last_sent_input = intent

                # Predict locally only for real player input
                if intent & DIR_MASK:
                    client._prediction(intent, FIXED_DT)

            tick_accumulator -= FIXED_DT

        intent &= ~SHOOT

        client.draw(screen)
        await display_fps(screen, clock)

        if IN_CHAT:
            await load_chat_box(
                screen,
                CHAT_STATE["current_input"],
                CHAT_STATE["focused"],
                CHAT_STATE["scroll_offset"]
            )

        pygame.display.flip()
        clock.tick(60)
        await asyncio.sleep(0)

    client.send_disconnect()
    await asyncio.sleep(0.1)
    pygame.quit()


async def discover_manager():
    global MANAGER_PORT, MANAGER_IP, MANAGER_HOST
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", 37022))
    sock.setblocking(False)  # Needed for async
    loop = asyncio.get_running_loop()
    while True:
        data, addr = await loop.sock_recvfrom(sock, 1024)
        info = json.loads(data.decode())

        if info.get("service") == "mm0Rgb-!#sErv-7":
            MANAGER_PORT = info["port"]
            MANAGER_IP = info.get("ip", addr[0])
            MANAGER_HOST = info["host"]
            sock.close()
            return info["host"]


async def main():
    global CURRENT_CLIENT, RECONNECT_INFO

    server_host = await discover_manager()
    login_result = await show_login_window()
    if not login_result:
        return

    try:
        configuration = QuicConfiguration(is_client=True, alpn_protocols=["mmo"])
        configuration.verify_mode = ssl.CERT_REQUIRED
        configuration.load_verify_locations("ca.cert.pem")
        configuration.server_name = server_host

        protocol_factory = lambda *args, **kwargs: GameClientProtocol(
            login_result["token"], [], 0, 0, 0, 0, 0, *args, **kwargs
        )

        # Manually enter the first connection
        manager = connect(
            login_result["server_ip"], login_result["server_port"],
            configuration=configuration,
            create_protocol=protocol_factory,
            stream_handler=None
        )
        CURRENT_CLIENT = await manager.__aenter__()

        timeout_timer = 0.0
        while not CURRENT_CLIENT.connected:
            await asyncio.sleep(0.01)
            timeout_timer += 0.01
            if timeout_timer > 10.0:
                return

        # Start game loop (it will now run forever)
        await game_loop()

    except Exception as e:
        _log_obfuscated_error("main", e)
        return
    finally:
        if CURRENT_CLIENT:
            CURRENT_CLIENT.protocol.close()
        pygame.quit()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        sys.exit(0)