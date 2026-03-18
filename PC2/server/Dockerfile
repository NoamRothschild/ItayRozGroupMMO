FROM python:3.11

WORKDIR /app

# Install the library you are using
RUN pip install aioquic psutil

# Copy your code, certificates, and map file
COPY . .

# Save (Expose) the 2 ports you need.
# We specify /udp because QUIC uses UDP, not TCP.
EXPOSE 4433/udp

CMD ["python","-u", "MMORPGServer.py"]