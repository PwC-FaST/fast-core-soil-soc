import os
import json
import numpy as np
import shutil
import threading
import rasterio
import re
import requests
import time
import traceback

from pyunpack import Archive
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
        archive_format, archive_url, source_id = Helpers.parse_body(context,body)

        # create temporary directory
        tmp_dir = Helpers.create_temporary_dir(context,event)

        archive_name = archive_url.rsplit('/',1)[-1]
        archive_target_path = os.path.join(tmp_dir,archive_name)

        # download requested archive
        context.logger.info("Start downloading archive '{0}' ...".format(archive_url))
        Helpers.download_file(context,archive_url,archive_target_path)

        # extract archive
        context.logger.info("Start extracting archive ...")
        Archive(archive_target_path).extractall(tmp_dir)
        context.logger.info("Archive successfully extracted !")

        # search TIFF files
        tiffs = []
        search_path = tmp_dir
        target_files = FunctionConfig.target_files
        for dirpath, dirnames, files in os.walk(search_path):
            for f in [file for file in files if file in target_files]:
                tiffs.append(os.path.join(dirpath,f))

        # check if we have the expected number of TIFF files
        if not len(tiffs) == len(target_files):
            context.logger.warn_with("Expected to find '{0}' files in archive '{1}', found={2}"
                .format(target_files,archive_name,tiffs))
            raise NuclioResponseError(
                "Expected to find '{0}' files in archive '{1}', found={2}".format(target_files,archive_name,tiffs),
                requests.codes.bad)

        # TIFF file to process
        f = tiffs[0]

        # kafka settings
        p = FunctionState.producer
        target_topic = FunctionConfig.target_topic

        # read TIFF file
        r = rasterio.open(f)
        b = r.read(1, masked=True)

        # get the mask (inverse of the GDAL band mask)
        m = ~b.mask

        # process raster data
        count = 0
        crs = r.crs.to_string()
        # set legal CRS (hard coded EPSG code ...)
        legal_crs = FunctionConfig.legal_eu_epsg_code
        context.logger.info('Processing TIFF file {0} ...'.format(os.path.basename(f)))
        start = time.time()
        for x in np.ndenumerate(b):
            (x,y), v = x
            if (m[x,y]):
                lat, lon = r.xy(x,y)

                # generate a basic pixel ID to enable idempotent ingestion
                pixel_id = "{0}:{1}:{2}".format(source_id,x,y);

                properties = {
                    "soc": float(v),
                    "crs": {
                        "type": "PROJ4",
                        "properties": {
                            "string": crs
                        }
                    },
                    "legal_crs": {
                        "type": "EPSG",
                        "properties": {
                            "code": legal_crs
                        }
                    }
                }

                geometry = {
                    "type": "Point",
                    "coordinates": [lat, lon]
                }

                # forge a GeoJSON feature
                feature = dict(_id=pixel_id, type="Feature", geometry=geometry, properties=properties)

                # send the GeoJSON feature
                value = json.dumps(feature).encode("utf-8-sig")
                p.produce(target_topic, value)
                if count % 1000 == 0:
                    p.flush()

                count += 1

        p.flush()
        context.logger.info('TIFF file {0} processed successfully ({1} pixels in {2}s).'
            .format(os.path.basename(f),count,round(time.time()-start)))

    except NuclioResponseError as error:
        return error.as_response(context)

    except Exception as error:
        context.logger.warn_with('Unexpected error occurred, responding with internal server error',
            exc=str(error))
        message = 'Unexpected error occurred: {0}\n{1}'.format(error, traceback.format_exc())
        return NuclioResponseError(message).as_response(context)

    finally:
        shutil.rmtree(tmp_dir)

    return "Processing Completed ({0} pixels)".format(count)


class FunctionConfig(object):

    kafka_bootstrap_server = None

    target_topic = None

    accepted_source_id = 'soc:esdac'

    target_files = [
        "ocCont_snap.tif"]

    legal_eu_epsg_code = '3035'


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

        # parse archive's format
        if not ('format' in body and body['format'].lower() == 'zip'):
            context.logger.warn_with('Archive\'s \'format\' must be \'ZIP\' !')
            raise NuclioResponseError(
                'Archive\'s \'format\' must be \'ZIP\' !',requests.codes.bad)
        archive_format = body['format']

        # parse archive's URL
        if not 'url' in body:
            context.logger.warn_with('Missing \'url\' attribute !')
            raise NuclioResponseError(
                'Missing \'url\' attribute !',requests.codes.bad)
        archive_url = body['url']

        # parse archive's sourceID
        if not 'sourceID' in body:
            context.logger.warn_with('Missing \'sourceID\' attribute !')
            raise NuclioResponseError(
                'Missing \'sourceID\' attribute !',requests.codes.bad)

        if not body['sourceID'].startswith(FunctionConfig.accepted_source_id):
            context.logger.warn_with("sourceID: not a '{0}' data source !".format(FunctionConfig.accepted_source_id))
            raise NuclioResponseError(
                "sourceID: not a '{0}' data source !".format(FunctionConfig.accepted_source_id),requests.codes.bad)
        source_id = body['sourceID']

        return archive_format, archive_url, source_id

    @staticmethod
    def create_temporary_dir(context, event):
        """
        Creates a uniquely-named temporary directory (based on the given event's id) and returns its path.
        """
        temp_dir = '/tmp/nuclio-event-{0}'.format(event.id)
        os.makedirs(temp_dir)

        context.logger.debug_with('Temporary directory created', path=temp_dir)

        return temp_dir

    @staticmethod
    def download_file(context, url, target_path):
        """
        Downloads the given remote URL to the specified path.
        """
        # make sure the target directory exists
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        try:
            with requests.get(url, stream=True) as response:
                response.raise_for_status()
                with open(target_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
        except Exception as error:
            if context is not None:
                context.logger.warn_with('Failed to download file',
                                         url=url,
                                         target_path=target_path,
                                         exc=str(error))
            raise NuclioResponseError('Failed to download file: {0}'.format(url),
                                      requests.codes.service_unavailable)
        if context is not None:
            context.logger.info_with('File downloaded successfully',
                                     size_bytes=os.stat(target_path).st_size,
                                     target_path=target_path)

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
        return context.Response(body=self._description,
                                headers={},
                                content_type='text/plain',
                                status_code=self._status_code)


t = threading.Thread(target=Helpers.on_import)
t.start()
