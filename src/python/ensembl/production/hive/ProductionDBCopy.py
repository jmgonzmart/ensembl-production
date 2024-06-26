#  See the NOTICE file distributed with this work for additional information
#  regarding copyright ownership.
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#      http://www.apache.org/licenses/LICENSE-2.0
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.

import json
import time
from urllib.parse import urlparse

from ensembl.hive.HiveRESTClient import HiveRESTClient

from .BaseProdRunnable import BaseProdRunnable


class ProductionDBCopy(HiveRESTClient, BaseProdRunnable):
    """ Production DB copy REST Hive Client: """

    def fetch_input(self):
        src_db = urlparse(self.param('source_db_uri'))
        tgt_db = urlparse(self.param('target_db_uri'))
        if src_db and tgt_db and src_db.scheme and src_db.hostname and tgt_db.hostname and tgt_db.scheme:
            # only if both parameters are set and valid
            self.param('payload', json.dumps({
                "src_host": ':'.join((src_db.hostname, str(src_db.port))),
                "src_incl_db": src_db.path[1:],
                "tgt_host": ':'.join((tgt_db.hostname, str(tgt_db.port))),
                "tgt_db_name": tgt_db.path[1:],
                "user": self.param('user')
            }))
        # Trigger http request with the following parameters:
        #   method: self.param_required('method')
        #   url: self.param_required('endpoint')
        #   headers: self.param('headers')
        #   data: self.param('payload')
        #   timeout: self.param('endpoint_timeout')
        super().fetch_input()
        response = self.param("response")
        response_body = response.json()
        if not response_body.get("job_id"):
            response_code = response.status_code
            method = self.param_required("method")
            endpoint = self.param_required("endpoint")
            payload = self.param("payload")
            err_msg = "Copy submission failed. The server did not return a job_id."
            http_request = f"Request: HTTP {method} {endpoint} -- {payload}"
            http_response = f"Response: HTTP {response_code} -- {response_body}"
            raise IOError(f"{err_msg} {http_request} {http_response}")

    def run(self):
        response = self.param('response')
        job_id = response.json()['job_id']
        if isinstance(job_id, list):
            job_id = job_id[0]
        submitted_time = time.time()
        payload = json.loads(self.param('payload'))
        while True:
            with self._session_scope() as http:
                job_response = http.request(
                    method='get',
                    url=f"{self.param('endpoint')}/{job_id}",
                    headers=self.param('headers'),
                    timeout=self.param('endpoint_timeout')
                )
            # job progress
            runtime = time.time() - submitted_time
            # message is a dict as follow:
            # "detailed_status": {
            #   "status_msg": "Complete",
            #   "progress": 100.0,
            #   "total_tables": 77,
            #   "table_copied": 77
            # }
            #
            message = job_response.json().get("detailed_status", {})
            message.update({"runtime": str(runtime)})
            self.write_progress(message)
            if job_response.json()['overall_status'] == 'Failed':
                raise IOError(
                    f"The Copy failed, check: {self.param('endpoint')}/{job_id}"
                )
            if job_response.json()['overall_status'] == 'Complete':
                break
            # Pause for 1min before checking copy status again
            time.sleep(60)

        runtime = time.time() - submitted_time
        output = {
            'source_db_uri': f"{payload['src_host']}/{payload['src_incl_db']}",
            'target_db_uri': payload['tgt_host'],
            'runtime': str(runtime)
        }
        # in coordination with the dataflow output set in config to "?table_name=results",
        # this will insert results in hive db. Remember @Luca/@marc conversation.
        self.write_result(output)

    def process_response(self, response):
        pass
