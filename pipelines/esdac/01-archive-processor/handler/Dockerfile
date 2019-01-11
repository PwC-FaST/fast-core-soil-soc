ARG DOCKER_REPO=pwcasdnmp.azurecr.io
ARG NUCLIO_BASE_IMAGE=library/python-processor
ARG NUCLIO_BASE_IMAGE_TAG=alpine-3.6

# From the base image
FROM ${DOCKER_REPO}/${NUCLIO_BASE_IMAGE}:${NUCLIO_BASE_IMAGE_TAG}

ADD requirements-0.txt .
ADD requirements-1.txt .

RUN apk --no-cache add \
    	build-base  \
        p7zip \
        gdal \
		gdal-dev \
    	librdkafka-dev \ 
    	musl-dev \
	    --repository http://dl-3.alpinelinux.org/alpine/edge/main/ --allow-untrusted \
    	--repository http://dl-3.alpinelinux.org/alpine/edge/testing/ --allow-untrusted && \
    pip install -r requirements-0.txt && \
    pip install -r requirements-1.txt && \
    apk del build-base gdal-dev gcc musl-dev

ADD main.py /opt/nuclio
