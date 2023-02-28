FROM debian

WORKDIR /app

ADD . .

RUN apt update \
&& apt -y install wget gnupg2 \
&&  apt update \
&&  apt -y upgrade \
&&  apt install -y -o 'Acquire::Retries=3' python3-pip build-essential gdal-bin libglu1-mesa libglib2.0-0 python3-tk libgdal-dev gdal-data x11-apps xdg-utils \
&&  apt-get install -y -o 'Acquire::Retries=3' \
	libfreetype6 \
	libfreetype6-dev \
	libgeos-dev \
	libpng-dev \
	libspatialindex-dev \
	qt5-style-plugins \
	python3-dev \
	python3-gdal \
	python3-pip \
	python3-pyqt5 \
	python3-pyqt5.qtopengl \
	python3-simplejson \
	python3-tk \
	sudo \
&&  pip install PyQt5-sip pyqt5 networkx \
&&  pip install -r requirements-working.txt \
&&  rm -rf /var/lib/apt/lists/* \
&& useradd user && mkdir /home/user

CMD sudo -u user python3 /app/FlatCAM.py
