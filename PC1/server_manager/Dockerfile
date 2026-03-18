FROM python:3.11

WORKDIR /app

RUN pip install aioquic

COPY . .

# Save (Expose) the 1 port you need
EXPOSE 9999/udp
EXPOSE 37025/udp
EXPOSE 37022/udp

CMD ["python","-u", "MMORPGManager.py"]