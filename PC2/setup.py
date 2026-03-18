import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.connect(("8.8.8.8", 80))
ip = sock.getsockname()[0]
sock.close()

with open(".env", "w") as f:
    f.write(f"PUBLIC_IP={ip}\n")

print(f"Detected IP: {ip}")
