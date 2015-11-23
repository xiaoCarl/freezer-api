"""
Copyright 2015 Hewlett-Packard

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.

"""

import falcon
import uuid

from freezer_api.common import exceptions as freezer_api_exc
from freezer_api.api.common import resource


class JobsBaseResource(resource.BaseResource):
    """
    Base class able to create actions contained in a job document

    """
    def __init__(self, storage_driver):
        self.db = storage_driver

    def get_action(self, user_id, action_id):
        found_action = None
        try:
            found_action = self.db.get_action(
                user_id=user_id, action_id=action_id)
        except freezer_api_exc.DocumentNotFound:
            pass
        return found_action

    def update_actions_in_job(self, user_id, job_doc):
        """
        Looks into a job document and creates actions in the db.
        Actions are given an action_id if they don't have one yet
        """
        job = Job(job_doc)
        for action in job.actions():
            if action.action_id:
                # action has action_id, let's see if it's in the db
                found_action_doc = self.get_action(
                    user_id=user_id, action_id=action.action_id)
                if found_action_doc:
                    if action == Action(found_action_doc):
                        # action already present in the db, do nothing
                        continue
                    else:
                        # action is different, generate new action_id
                        action.action_id = ''
                # action not found in db, leave current action_id
            self.db.add_action(user_id=user_id, doc=action.doc)


class JobsCollectionResource(JobsBaseResource):
    """
    Handler for endpoint: /v1/jobs
    """

    def on_get(self, req, resp):
        # GET /v1/jobs(?limit,offset)     Lists jobs
        user_id = req.get_header('X-User-ID')
        offset = req.get_param_as_int('offset') or 0
        limit = req.get_param_as_int('limit') or 10
        search = self.json_body(req)
        obj_list = self.db.search_job(user_id=user_id, offset=offset,
                                      limit=limit, search=search)
        resp.body = {'jobs': obj_list}

    def on_post(self, req, resp):
        # POST /v1/jobs    Creates job entry
        try:
            doc = self.json_body(req)
        except KeyError:
            raise freezer_api_exc.BadDataFormat(
                message='Missing request body')

        user_id = req.get_header('X-User-ID')
        self.update_actions_in_job(user_id, doc)
        job_id = self.db.add_job(user_id=user_id, doc=doc)
        resp.status = falcon.HTTP_201
        resp.body = {'job_id': job_id}


class JobsResource(JobsBaseResource):
    """
    Handler for endpoint: /v1/jobs/{job_id}
    """

    def on_get(self, req, resp, job_id):
        # GET /v1/jobs/{job_id}     retrieves the specified job
        # search in body
        user_id = req.get_header('X-User-ID') or ''
        obj = self.db.get_job(user_id=user_id, job_id=job_id)
        if obj:
            resp.body = obj
        else:
            resp.status = falcon.HTTP_404

    def on_delete(self, req, resp, job_id):
        # DELETE /v1/jobs/{job_id}     Deletes the specified job
        user_id = req.get_header('X-User-ID')
        self.db.delete_job(user_id=user_id, job_id=job_id)
        resp.body = {'job_id': job_id}
        resp.status = falcon.HTTP_204

    def on_patch(self, req, resp, job_id):
        # PATCH /v1/jobs/{job_id}     updates the specified job
        user_id = req.get_header('X-User-ID') or ''
        doc = self.json_body(req)
        self.update_actions_in_job(user_id, doc)
        new_version = self.db.update_job(user_id=user_id,
                                         job_id=job_id,
                                         patch_doc=doc)
        resp.body = {'job_id': job_id, 'version': new_version}

    def on_post(self, req, resp, job_id):
        # PUT /v1/jobs/{job_id}     creates/replaces the specified job
        user_id = req.get_header('X-User-ID') or ''
        doc = self.json_body(req)
        self.update_actions_in_job(user_id, doc)
        new_version = self.db.replace_job(user_id=user_id,
                                          job_id=job_id,
                                          doc=doc)
        resp.status = falcon.HTTP_201
        resp.body = {'job_id': job_id, 'version': new_version}


class JobsEvent(resource.BaseResource):
    """
    Handler for endpoint: /v1/jobs/{job_id}/event

    Actions are passed in the body, for example:
    {
        "start": null
    }
    """
    def __init__(self, storage_driver):
        self.db = storage_driver

    def on_post(self, req, resp, job_id):
        # POST /v1/jobs/{job_id}/event
        # requests an event on the specified job

        user_id = req.get_header('X-User-ID') or ''
        doc = self.json_body(req)

        try:
            event, params = next(doc.iteritems())
        except:
            raise freezer_api_exc.BadDataFormat("Bad event request format")

        job_doc = self.db.get_job(user_id=user_id,
                                  job_id=job_id)
        job = Job(job_doc)
        result = job.execute_event(event, params)

        if job.need_update:
            self.db.replace_job(user_id=user_id,
                                job_id=job_id,
                                doc=job.doc)
        resp.status = falcon.HTTP_202
        resp.body = {'result': result}


class Action(object):
    def __init__(self, doc):
        self.doc = doc

    @property
    def action_id(self):
        return self.doc.get('action_id', '')

    @action_id.setter
    def action_id(self, value):
        self.doc['action_id'] = value

    def create_new_action_id(self):
        self.doc['action_id'] = uuid.uuid4().hex

    def __eq__(self, other):
        # return self.doc == other.doc
        dont_care_keys = ['_version', 'user_id']
        lh = self.doc.get('freezer_action', None)
        rh = other.doc.get('freezer_action', None)
        diffkeys = [k for k in lh if lh[k] != rh.get(k)]
        diffkeys += [k for k in rh if rh[k] != lh.get(k)]
        for k in diffkeys:
            if k not in dont_care_keys:
                return False
        return True

    def __ne__(self, other):
        return not (self.__eq__(other))


class Job(object):
    """
    A class with knowledge of the inner working of a job data structure.

    Responibilities:
     - manage the events that can be sent to a job. The result of handling
       an event is a modification of the information contained in the
       job document
     - extract actions from a job (usage example: to be used to create actions)
    """
    def __init__(self, doc):
        self.doc = doc
        self.event_result = ''
        self.need_update = False
        if 'job_schedule' not in doc:
            doc['job_schedule'] = {}
        self.job_schedule = doc['job_schedule']
        self.event_handlers = {'start': self.start,
                               'stop': self.stop,
                               'abort': self.abort}

    def execute_event(self, event, params):
        handler = self.event_handlers.get(event, None)
        if not handler:
            raise freezer_api_exc.BadDataFormat("Bad Action Method")
        try:
            self.event_result = handler(params)
        except freezer_api_exc.BadDataFormat:
            raise
        except Exception as e:
            raise freezer_api_exc.FreezerAPIException(e)
        return self.event_result

    @property
    def job_status(self):
        return self.job_schedule.get('status', '')

    @job_status.setter
    def job_status(self, value):
        self.job_schedule['status'] = value

    def start(self, params=None):
        if self.job_status in ["scheduled", "running"]:
            return 'already active'
        if self.job_status in ["completed", "stop", ""]:
            # completed jobs are not acquired by the scheduler
            self.job_status = 'stop'
            self.job_schedule['event'] = 'start'
            self.job_schedule['result'] = ''
            self.need_update = True
            return 'success'
        else:
            raise freezer_api_exc.BadDataFormat("unable to start a {0} job"
                                                .format(self.job_status))

    def stop(self, params=None):
        if self.job_status in ["scheduled", "running", ""]:
            self.job_schedule['event'] = 'stop'
            self.need_update = True
            return 'success'
        else:
            return 'already stopped'

    def abort(self, params=None):
        if self.job_status in ["scheduled", "running", ""]:
            self.job_schedule['event'] = 'abort'
            self.need_update = True
            return 'success'
        else:
            return 'already stopped'

    def actions(self):
        """
        Generator to iterate over the actions contained in a job

        :return: yields Action objects
        """
        for action_doc in self.doc.get('job_actions', []):
            yield Action(action_doc)