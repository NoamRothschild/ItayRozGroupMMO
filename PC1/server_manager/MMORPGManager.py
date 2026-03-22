import asyncio
import json
import socket
import time
import aiosqlite
import sqlite3
import uuid
import hashlib
from aioquic.asyncio import serve
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.events import StreamDataReceived, ConnectionTerminated


MANAGER_PORT = 9999
BROADCAST_PORT = 37025
ALPN = "manager-proto"
SERVER_SECRET = "s3Rv-K3y!@2026x"  # Shared secret — servers must send this to register

PLAYER_WIDTH = 37
PLAYER_HEIGHT = 56

CHUNK_WIDTH = 1920
CHUNK_HEIGHT = 1080
MAP_WIDTH = 76800
MAP_HEIGHT = 43200

UNASSIGNED_CHUNKS = [(x, y) for x in range(-MAP_WIDTH // 2, MAP_WIDTH // 2, CHUNK_WIDTH)
                     for y in range(-MAP_HEIGHT // 2, MAP_HEIGHT // 2, CHUNK_HEIGHT)]

SERVER_TIMEOUT = 15

ACTIVE_SERVERS = {}
CHUNK_OWNERS = {}

MESSAGE_QUEUE = None

ONLINE_PLAYERS = []
ONLINE_PLAYERS_IDS_INDEX = {}

async def init_db():
    async with aiosqlite.connect("db.db") as conn:
        await conn.execute("PRAGMA journal_mode=WAL;")
        await conn.execute("PRAGMA busy_timeout = 5000;")

        await conn.execute(f'''
            CREATE TABLE IF NOT EXISTS players (
                player_id TEXT PRIMARY KEY,
                username TEXT,
                password TEXT,
                x INTEGER DEFAULT {-PLAYER_WIDTH//2},
                y INTEGER DEFAULT {-PLAYER_HEIGHT//2},
                hp INTEGER DEFAULT 100,
                bow INTEGER DEFAULT 0,
                heal INTEGER DEFAULT 0,
                strength INTEGER DEFAULT 0,
                shield INTEGER DEFAULT 0,
                active_weapon_id INTEGER DEFAULT 9
            )
        ''')
        await conn.commit()

    print("Database initialized")

def db_read(query, params=()):
    """Runs a read query in a separate thread to prevent blocking the async loop."""
    with sqlite3.connect('db.db') as conn:
        return conn.execute(query, params).fetchall()

def db_write(query, params=()):
    """Runs a write query in a separate thread."""
    with sqlite3.connect('db.db') as conn:
        conn.execute(query, params)
        conn.commit()

class ManagerProtocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_addr = None
        self.stream_buffers = {}  # [FIXED] Stream Buffer Dict

    def quic_event_received(self, event):
        if isinstance(event, StreamDataReceived):
            buf = self.stream_buffers.setdefault(event.stream_id, bytearray())
            buf.extend(event.data)

            # [FIXED] Split streams by newline to prevent MTU crashes
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                self.stream_buffers[event.stream_id] = buf
                if not line: continue

                try:
                    if self in ACTIVE_SERVERS:
                        ACTIVE_SERVERS[self]["last_seen"] = time.monotonic()
                    MESSAGE_QUEUE.put_nowait((self, line, self.client_addr[0], event.stream_id))
                except asyncio.QueueFull:
                    print("[MANAGER] Queue full! Dropping packet.")

            # 2. Process legacy Client requests (like AUTH_REQUEST) that don't use newlines but end the stream
            if event.end_stream and buf:
                line = buf
                self.stream_buffers[event.stream_id] = bytearray()
                try:
                    if self in ACTIVE_SERVERS:
                        ACTIVE_SERVERS[self]["last_seen"] = time.monotonic()
                    MESSAGE_QUEUE.put_nowait((self, line, self.client_addr[0], event.stream_id))
                except asyncio.QueueFull:
                    print("[MANAGER] Queue full! Dropping packet.")

        elif isinstance(event, ConnectionTerminated):
            if self in ACTIVE_SERVERS:
                srv = ACTIVE_SERVERS.pop(self)
                print(f"[MANAGER] Server disconnected: {srv['id']}")
                for chunk in srv['chunks']:
                    chunk_tuple = tuple(chunk)
                    CHUNK_OWNERS.pop(chunk_tuple, None)
                    if chunk_tuple not in UNASSIGNED_CHUNKS:
                        UNASSIGNED_CHUNKS.append(chunk_tuple)
                print(f"[MANAGER] Reclaimed {len(srv['chunks'])} chunks.")

    def datagram_received(self, data, addr):
        # Capture the address (IP, Port) from the incoming packet
        self.client_addr = addr
        # Pass it up to the parent class so QUIC still works
        super().datagram_received(data, addr)


def get_chunk_for_pos(x, y):
    chunk_x = (int(x) // CHUNK_WIDTH) * CHUNK_WIDTH
    chunk_y = (int(y) // CHUNK_HEIGHT) * CHUNK_HEIGHT
    return (chunk_x, chunk_y)


def get_authoritative_server_for_chunk(x, y):
    chunk_pos = get_chunk_for_pos(x, y)
    if chunk_pos in CHUNK_OWNERS:
        return CHUNK_OWNERS[chunk_pos]
    if ACTIVE_SERVERS:
        all_srvs = list(ACTIVE_SERVERS.items())
        all_srvs.sort(key=lambda s: s[1]["load"])
        return all_srvs[0][0]
    return None


def hash_password(password):
    """
    Triple-layered password hashing:
      Layer 1 : SHA-256   (industry standard baseline)
      Layer 2 : SHA3-512  (Keccak family – different algorithm family from SHA-2)
      Layer 3 : BLAKE2b-512 (modern, competition winner, unrelated to SHA families)
    Each layer feeds its hex-digest into the next, so cracking any single
    algorithm is not enough to recover the original password.
    """
    layer1 = hashlib.sha256(password.encode()).hexdigest()
    layer2 = hashlib.sha3_512(layer1.encode()).hexdigest()
    layer3 = hashlib.blake2b(layer2.encode()).hexdigest()
    return layer3


def check_input(username, password):
    str_check = username + password
    bad_chars = [
        "1=1", "\'a\'=\'a\'", "'", '"', "OR TRUE", "\' OR 1=1 --",
        "\" OR \"\"=\"", "admin\' --", "\' OR \'1\'=\'1\' #", "@",
        "*", "#", "\\", "\' --", "\' OR ", "; DROP TABLE users; --",
        "\'; SHUTDOWN; --", "CHAR("
    ]
    for bc in bad_chars:
        if bc in str_check:
            return False
    return True


async def handle_auth(username, password, mode):
    if not check_input(username, password):
        return {"success": False, "msg": "Invalid characters in input"}

    async with aiosqlite.connect("db.db") as conn:
        await conn.execute("PRAGMA busy_timeout = 5000;")
        if mode == "login":
            hashed_pw = hash_password(password)
            async with conn.execute(
                """SELECT player_id, x, y, hp, bow, heal, strength, shield, active_weapon_id
                   FROM players
                   WHERE username=? AND password=?""",
                (username, hashed_pw)
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                pid, x, y, hp, bow, heal, strength, shield, active_weapon_id = row

                best_conn = get_authoritative_server_for_chunk(x, y)

                if best_conn:
                    srv = ACTIVE_SERVERS[best_conn]
                    reservation_token = str(uuid.uuid4())

                    payload = json.dumps({
                        "type": "EXPECTED_PLAYER",
                        "token": reservation_token,
                        "pid": pid,
                        "user": username,
                        "x": x,
                        "y": y,
                        "hp": hp,
                        "bow": bow,
                        "heal": heal,
                        "strength": strength,
                        "shield": shield,
                        "active_weapon_id": active_weapon_id
                    }).encode() + b'\n'

                    ONLINE_PLAYERS.append((username, password))
                    ONLINE_PLAYERS_IDS_INDEX[pid] = (username, password)

                    stream_id = best_conn._quic.get_next_available_stream_id()
                    best_conn._quic.send_stream_data(stream_id, payload, end_stream=True)
                    best_conn.transmit()

                    return {
                        "success": True,
                        "server_ip": srv["ip"],
                        "server_port": srv["port"],
                        "token": reservation_token
                    }
                else:
                    return {"success": False, "msg": "No server available for this region"}

            return {"success": False, "msg": "Invalid Credentials"}

        elif mode == "signup":
            try:
                x, y = -PLAYER_WIDTH // 2, -PLAYER_HEIGHT // 2
                pid = str(uuid.uuid4())

                async with conn.execute(
                    "SELECT username FROM players WHERE username=?",
                    (username,)
                ) as cursor:
                    existing = await cursor.fetchone()

                if existing:
                    return {"success": False, "msg": "Username already exists"}

                hashed_pw = hash_password(password)
                await conn.execute(
                    """INSERT INTO players
                       (player_id, username, password, x, y, hp, bow, heal, strength, shield, active_weapon_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pid, username, hashed_pw, x, y, 100, 0, 0, 0, 0, 9)
                )
                await conn.commit()
                await conn.close()

                best_conn = get_authoritative_server_for_chunk(x, y)
                if best_conn:
                    srv = ACTIVE_SERVERS[best_conn]
                    reservation_token = str(uuid.uuid4())

                    payload = json.dumps({
                        "type": "EXPECTED_PLAYER",
                        "token": reservation_token,
                        "pid": pid,
                        "user": username,
                        "x": x,
                        "y": y,
                        "hp": 100,
                        "bow": 0,
                        "heal": 0,
                        "strength": 0,
                        "shield": 0,
                        "active_weapon_id": 9
                    }).encode() + b'\n'

                    ONLINE_PLAYERS.append((username, password))
                    ONLINE_PLAYERS_IDS_INDEX[pid] = (username, password)

                    stream_id = best_conn._quic.get_next_available_stream_id()
                    best_conn._quic.send_stream_data(stream_id, payload, end_stream=True)
                    best_conn.transmit()

                    return {
                        "success": True,
                        "server_ip": srv["ip"],
                        "server_port": srv["port"],
                        "token": reservation_token
                    }
                else:
                    return {"success": False, "msg": "Account Created, but no Servers Online."}

            except sqlite3.IntegrityError:
                return {"success": False, "msg": "Username Taken"}
            except Exception as e:
                return {"success": False, "msg": str(e)}

        else:
            return {"success": False, "msg": "Error"}


# Worker Task to process messages
async def packet_processor_worker():
    print("[MANAGER] Worker started...")
    while True:
        # This waits until an item is available
        quic_conn, data, sender_ip, stream_id = await MESSAGE_QUEUE.get()

        try:
            # HEAVY WORK happens here, off the main network thread
            msg = json.loads(data.decode())
            await handle_message(quic_conn, msg, sender_ip, stream_id)
        except Exception as e:
            print(f"[MANAGER] Error processing packet: {e}")
        finally:
            MESSAGE_QUEUE.task_done()


async def handle_message(client, msg, sender_ip, stream_id):
    t = msg.get("type")

    if t == "SERVER_HELLO":
        # Authenticate: server must provide the shared secret
        if msg.get("secret") != SERVER_SECRET:
            print(f"[MANAGER] REJECTED Server {sender_ip}: Invalid server secret.")
            return

        if not UNASSIGNED_CHUNKS and not ACTIVE_SERVERS:
            print(f"[MANAGER] REJECTED Server {sender_ip}: Map is fully hosted.")
            return

        server_id = msg["id"]
        server_port = msg["port"]
        mesh_port = msg["mesh_port"]

        # FIX: Extract the actual PUBLIC_IP from the message instead of Docker's sender_ip!
        real_ip = msg.get("public_ip", sender_ip)

        assigned = []
        for _ in range(min(400, len(UNASSIGNED_CHUNKS))):
            chunk = UNASSIGNED_CHUNKS.pop(0)
            assigned.append(chunk)
            CHUNK_OWNERS[chunk] = client

        ACTIVE_SERVERS[client] = {
            "id": server_id, "ip": real_ip, "port": server_port,  # <-- Uses real_ip
            "mesh_port": mesh_port, "chunks": assigned,
            "load": 0, "last_seen": time.monotonic()
        }

        print(f"[MANAGER] Registered {server_id} with {len(assigned)} chunks at {real_ip}.")

        directory = {}
        for conn, info in ACTIVE_SERVERS.items():
            if conn != client:
                directory[info["id"]] = f"{info['ip']}:{info['mesh_port']}"

        packet = {
            "type": "CHUNK_ASSIGNMENT",
            "my_chunks": assigned,
            "directory": directory
        }
        client._quic.send_stream_data(stream_id, json.dumps(packet).encode() + b'\n', end_stream=False)
        client.transmit()

        new_peer = {
            "type": "NEW_PEER",
            "peer_id": server_id,
            "address": f"{real_ip}:{mesh_port}"  # <-- Uses real_ip here too
        }
        for conn in list(ACTIVE_SERVERS.keys()):
            if conn != client:
                s_id = conn._quic.get_next_available_stream_id(False)
                conn._quic.send_stream_data(s_id, json.dumps(new_peer).encode() + b'\n', end_stream=False)
                conn.transmit()

    elif t == "CHUNK_UPDATE":
        new_chunks = [tuple(c) for c in msg.get("chunks", [])]
        if client in ACTIVE_SERVERS:
            ACTIVE_SERVERS[client]["chunks"] = new_chunks
            for c in new_chunks:
                CHUNK_OWNERS[c] = client
                if c in UNASSIGNED_CHUNKS:
                    UNASSIGNED_CHUNKS.remove(c)

    elif t == "SERVER_SNAPSHOT":
        if client in ACTIVE_SERVERS:
            ACTIVE_SERVERS[client]["load"] = msg.get("load", 0)
            ACTIVE_SERVERS[client]["last_seen"] = time.monotonic()

            players = msg.get("players", [])
            async with aiosqlite.connect("db.db") as conn:
                await conn.execute("PRAGMA busy_timeout = 5000;")
                for p in players:
                    pid_db = str(uuid.UUID(hex=p["player_id"]))
                    await conn.execute(
                        "UPDATE players SET x=?, y=?, hp=? WHERE player_id=?",
                        (p["x"], p["y"], p["hp"], pid_db)
                    )
                await conn.commit()


    elif t == "AUTH_REQUEST":
        if not ACTIVE_SERVERS:
            response = {"success": False, "msg": "No active servers"}
            client._quic.send_stream_data(stream_id, json.dumps(response).encode(), end_stream=True)
            client.transmit()
            return

        username = msg["username"]
        password = msg["password"]
        mode = msg["mode"]  # "login" or "signup"

        if (username, password) in ONLINE_PLAYERS:
            response = {"success": False, "msg": "There is already an active user with that username/password"}
            client._quic.send_stream_data(stream_id, json.dumps(response).encode(), end_stream=True)
            client.transmit()
            return

        if len(username) > 16:
            response = {"success": False, "msg": "Username too long"}
            client._quic.send_stream_data(stream_id, json.dumps(response).encode(), end_stream=True)
            client.transmit()
            return

        if len(username.strip()) == 0 or len(password) == 0:
            response = {"success": False, "msg": "Username and password cannot be empty"}
            client._quic.send_stream_data(stream_id, json.dumps(response).encode(), end_stream=True)
            client.transmit()
            return

        if len(password) < 4:
            response = {"success": False, "msg": "Password must be at least 4 characters"}
            client._quic.send_stream_data(stream_id, json.dumps(response).encode(), end_stream=True)
            client.transmit()
            return

        response = await handle_auth(username, password, mode)

        # Send result back to client
        client._quic.send_stream_data(stream_id, json.dumps(response).encode(), end_stream=True)
        client.transmit()


    elif t == "PLAYER_LEFT":
        if client not in ACTIVE_SERVERS:
            return  # Only registered servers can report player disconnects

        pid_hex = msg["player_id"]
        x = msg.get("x")
        y = msg.get("y")
        hp = max(0, min(100, msg.get("hp", 100)))
        bow = 1 if msg.get("bow", 0) else 0
        heal = 1 if msg.get("heal", 0) else 0
        strength = 1 if msg.get("strength", 0) else 0
        shield = 1 if msg.get("shield", 0) else 0
        active_weapon_id = msg.get("active_weapon_id", 9)
        if active_weapon_id not in (1, 2, 3, 4, 5, 6, 9):
            active_weapon_id = 9

        pid_db = str(uuid.UUID(hex=pid_hex))
        async with aiosqlite.connect("db.db") as conn:
            await conn.execute("PRAGMA busy_timeout = 5000;")
            await conn.execute(
                """UPDATE players
                   SET x=?, y=?, hp=?, bow=?, heal=?, strength=?, shield=?, active_weapon_id=?
                   WHERE player_id=?""",
                (x, y, hp, bow, heal, strength, shield, active_weapon_id, pid_db)
            )
            await conn.commit()
        credentials = ONLINE_PLAYERS_IDS_INDEX.pop(pid_db, None)
        if credentials and credentials in ONLINE_PLAYERS:
            ONLINE_PLAYERS.remove(credentials)


async def reap_dead_servers():
    while True:
        await asyncio.sleep(10)
        now = time.monotonic()
        for quic_conn, info in list(ACTIVE_SERVERS.items()):
            if now - info["last_seen"] > SERVER_TIMEOUT:
                print(f"[MANAGER] Server timed out: {info['id']}")
                srv = ACTIVE_SERVERS.pop(quic_conn)
                for chunk in srv['chunks']:
                    chunk_tuple = tuple(chunk)
                    CHUNK_OWNERS.pop(chunk_tuple, None)
                    if chunk_tuple not in UNASSIGNED_CHUNKS:
                        UNASSIGNED_CHUNKS.append(chunk_tuple)
                quic_conn.close()

async def send_heartbeats():
    msg = {"type": "HEARTBEAT"}
    while True:
        for conn, _ in list(ACTIVE_SERVERS.items()):
            stream_id = conn._quic.get_next_available_stream_id(False)
            # [FIXED] Appended newline
            conn._quic.send_stream_data(stream_id, json.dumps(msg).encode() + b'\n', end_stream=False)
            conn.transmit()

        await asyncio.sleep(6)

def get_local_ip():
    import os
    # 1. Check if Docker passed in the IP via the environment variable first!
    env_ip = os.environ.get("PUBLIC_IP")
    if env_ip:
        print(f"[SERVER] Using IP from Docker environment: {env_ip}")
        return env_ip

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        print(f"[SERVER] Detected local IP: {ip}")

    except:
        ip = "127.0.0.1"
        print(f"[SERVER] Failed to detect IP, defaulting to: {ip}")
    finally:
        try:
            sock.close()
        except:
            pass

    return ip

async def broadcast_presence_to_client():
    broadcast_port_client = 37022
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)

    public_ip = get_local_ip()

    msg = json.dumps({
        "service": "mm0Rgb-!#sErv-7",
        "host": "game-server.local",
        "port": MANAGER_PORT,
        "ip": public_ip  # Send the Manager's IP inside the message
    }).encode()

    ip_parts = public_ip.split('.')
    if len(ip_parts) == 4:
        base_ip = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}."
    else:
        base_ip = "192.168.1."

    while True:
        try:
            sock.sendto(msg, ("255.255.255.255", broadcast_port_client))
        except Exception:
            pass

        for i in range(1, 255):
            try:
                sock.sendto(msg, (f"{base_ip}{i}", broadcast_port_client))
            except BlockingIOError:
                pass  # Buffer is full, skip
            except Exception:
                pass

            if i % 10 == 0:
                await asyncio.sleep(0)  # Let the async loop breathe!
        await asyncio.sleep(5)


async def broadcast_presence_to_server():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    public_ip = get_local_ip()

    msg = json.dumps({
        "id": "MANAGER_QUIC_AUTH",
        "host": "game-server.local",
        "port": MANAGER_PORT,
        "ip": public_ip  # Send the Manager's IP inside the message
    }).encode()

    ip_parts = public_ip.split('.')
    if len(ip_parts) == 4:
        base_ip = f"{ip_parts[0]}.{ip_parts[1]}.{ip_parts[2]}."
    else:
        base_ip = "192.168.1."

    while True:
        try:
            sock.sendto(msg, ("255.255.255.255", BROADCAST_PORT))
        except Exception:
            pass

        for i in range(1, 255):
            try:
                sock.sendto(msg, (f"{base_ip}{i}", BROADCAST_PORT))
            except Exception:
                pass

            if i % 10 == 0:
                await asyncio.sleep(0)  # Let the async loop breathe!
        await asyncio.sleep(5)


async def main():
    global MESSAGE_QUEUE
    MESSAGE_QUEUE = asyncio.Queue()
    config = QuicConfiguration(
        is_client=False,
        alpn_protocols=[ALPN],
        idle_timeout=300.0  # <--- NEW: Keep connection alive for 5 minutes
    )
    config.load_cert_chain(certfile="server.cert.pem", keyfile="server.key.pem")

    worker_task = asyncio.create_task(packet_processor_worker())

    try:
        await asyncio.gather(
            serve("0.0.0.0", MANAGER_PORT, configuration=config, create_protocol=ManagerProtocol),
            broadcast_presence_to_client(),
            broadcast_presence_to_server(),
            send_heartbeats(),
            reap_dead_servers(),
            worker_task
        )
    except asyncio.CancelledError:
        print("[MANAGER] Server stopped.")


async def startup():
    await init_db()
    await main()

if __name__ == "__main__":
    try:
        asyncio.run(startup())
    except KeyboardInterrupt:
        pass