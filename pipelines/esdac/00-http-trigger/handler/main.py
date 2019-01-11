import os
import json
import threading
import re
import requests
import traceback

from confluent_kafka import Producer, KafkaError

def handler(context, event):

    b = event.body
    if not isinstance(b, dict):
        body = json.loads(b.decode('utf-8-sig'))
    else:
        body = b
    
    context.logger.info("Event received: " + str(body))

    try:

        # if we're not ready to handle this request yet, deny it
        if not FunctionState.done_loading:
            context.logger.warn_with('Function not ready, denying request !')
            raise NuclioResponseError(
                'The service is loading and is temporarily unavailable.',requests.codes.unavailable)
        
        # parse event's payload
        url = Helpers.parse_body(context,body)

        # check archive format
        archive_to_donwload = url
        if not archive_to_donwload.endswith('.zip'):
            context.logger.warn_with("Not a ZIP archive, url(s)={0}".format(archive_to_donwload))
            raise NuclioResponseError(
                "Not a ZIP archive, url(s)={0}".format(archive_to_donwload),requests.codes.bad)

        # forge download command
        source_id = FunctionConfig.source_id
        download_cmd = {"format": "ZIP", "url": archive_to_donwload, "sourceID": source_id}

        # submit download command on a dedicated kafka topic
        p, target_topic = FunctionState.producer, FunctionConfig.target_topic
        value = json.dumps(download_cmd).encode("utf-8-sig")
        p.produce(target_topic, value)
        p.flush()

        context.logger.info('Download command successfully submitted: {0}'.format(value))

    except NuclioResponseError as error:
        return error.as_response(context)

    except Exception as error:
        context.logger.warn_with('Unexpected error occurred, responding with internal server error',
            exc=str(error))
        message = 'Unexpected error occurred: {0}\n{1}'.format(error, traceback.format_exc())
        return NuclioResponseError(message).as_response(context)

    return context.Response(body=json.dumps({"message": "Archive '{0}' successfully submitted for ingestion"
        .format(archive_to_donwload)}),
                            headers={},
                            content_type='application/json',
                            status_code=requests.codes.ok)


class FunctionConfig(object):

    kafka_bootstrap_server = None

    target_topic = None

    source_id = "soc:esdac"


class FunctionState(object):

    producer = None

    done_loading = False


class Helpers(object):

    @staticmethod
    def load_configs():

        FunctionConfig.kafka_bootstrap_server = os.getenv('KAFKA_BOOTSTRAP_SERVER')

        FunctionConfig.target_topic = os.getenv('TARGET_TOPIC')

    @staticmethod
    def load_producer():

        p = Producer({
            'bootstrap.servers':  FunctionConfig.kafka_bootstrap_server})

        FunctionState.producer = p

    @staticmethod
    def parse_body(context, body):

        # parse year
        if not 'url' in body:
            context.logger.warn_with('Missing \'url\' attribute !')
            raise NuclioResponseError(
                'Missing \'url\' attribute !',requests.codes.bad)
        url = body['url']

        return url

    @staticmethod
    def on_import():

        Helpers.load_configs()
        Helpers.load_producer()
        FunctionState.done_loading = True


class NuclioResponseError(Exception):

    def __init__(self, description, status_code=requests.codes.internal_server_error):
        self._description = description
        self._status_code = status_code

    def as_response(self, context):
        return context.Response(body=json.dumps({"message": self._description}),
                                headers={},
                                content_type='application/json',
                                status_code=self._status_code)


t = threading.Thread(target=Helpers.on_import)
t.start()
