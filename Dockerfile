FROM anasty17/mltb:latest

WORKDIR /usr/src/app
RUN chmod 777 /usr/src/app

RUN apt-get update && apt-get upgrade -y
RUN apt-get -qq install mediainfo -y

COPY Doit.txt .
RUN pip3 install --no-cache-dir -r Doit.txt

COPY . .

CMD ["bash", "start.sh"]
