#!/usr/bin/env python
import abc
import re
import json

import cachet_url_monitor.status as st
from cachet_url_monitor.exceptions import ConfigurationValidationError
from cachet_url_monitor.status import ComponentStatus


class Expectation(object):
    """Base class for URL result expectations. Any new expectation should extend
    this class and the name added to create() method.
    """

    @staticmethod
    def create(configuration, client):
        """Creates a list of expectations based on the configuration types
        list.
        """
        # If a need expectation is created, this is where we need to add it.
        expectations = {
            "HTTP_STATUS": HttpStatus,
            "LATENCY": Latency,
            "REGEX": Regex,
            "JSON_CHECK": JsonFieldValueCheck
        }
        if configuration["type"] not in expectations:
            raise ConfigurationValidationError(f"Invalid type: {configuration['type']}")

        return expectations.get(configuration["type"])(configuration, client)

    def __init__(self, configuration, client):
        self.incident_status = self.parse_incident_status(configuration)
        # component to be updated by this expectation
        self.component_id = configuration.get('component_id', None)
        self.independent = False
        if self.component_id is not None:
            self.componemt_name, self.component_status = client.get_component_name_and_status(
                self.component_id)
            self.previous_component_status = self.component_status
            self.current_fails = 0
            self.trigger_update = True
            self.message = ""
            self.allowed_fails = configuration.get("allowed_fails", None)
            self.independent = configuration.get('is_independent', False)

    def update_component_status(self, status: ComponentStatus, response):
        """Update the component status if any
        """
        if self.component_id is not None:
            self.previous_component_status = self.component_status
            self.component_status = status
            self.message = self.get_message(response)

    def is_independent(self) -> bool:
        """Get the independance status of the expecation. if True, does not contribute
        to the overall status aggregation of the endpoint.
        """
        if self.component_id is not None:
            return self.independent
        return False

    def get_current_status(self):
        """Get the current status of the expectation.
        """
        if self.component_id is not None:
            return self.component_status
        return None

    def if_trigger_update(self, allowed_fails, logger=None):
        """
        Checks if update should be triggered - trigger it for all operational states
        and only for non-operational ones above the configured threshold (allowed_fails).
        Only for expectation with component_id.
        """
        if self.component_id is None:
            return

        if self.allowed_fails is not None:
            allowed_fails = self.allowed_fails

        if self.component_status != st.ComponentStatus.OPERATIONAL:
            self.current_fails = self.current_fails + 1
            if logger is not None:
                logger.warning(f"Failure #{self.current_fails} with threshold set to {allowed_fails}")
            if self.current_fails <= allowed_fails:
                self.trigger_update = False
                return
        self.current_fails = 0
        self.trigger_update = True

    def push_status(self, client, logger=None):
        """Pushes the status of the component to the cachet server. It will update the component
        status based on the previous call to evaluate().
        """
        if self.component_id is None:
            return

        if self.previous_component_status == self.component_status:
            # We don't want to keep spamming if there's no change in status.
            if logger is not None:
                logger.info(f"No changes to component status.")
            self.trigger_update = False
            return

        self.previous_component_status = self.component_status

        if not self.trigger_update:
            return

        api_component_status = client.get_component_status(self.component_id)

        if self.component_status == api_component_status:
            return

        component_request = client.push_status(
            self.component_id, self.component_status)
        if component_request.ok:
            # Successful update
            if logger is not None:
                logger.info(f"Component update: status [{self.component_status}]")
        else:
            # Failed to update the API status
            if logger is not None:
                logger.warning(
                    f"Component update failed with HTTP status: {component_request.status_code}. API"
                    f" status: {self.component_status}"
                )

    def trigger_webhooks(self, endpoint_name, incident_title, webhooks, logger=None):
        """Trigger webhooks."""
        if self.component_id is None:
            return

        if self.component_status == st.ComponentStatus.PERFORMANCE_ISSUES:
            title = f'{endpoint_name} (Component {self.componemt_name}) degraded'
        elif self.component_status == st.ComponentStatus.OPERATIONAL:
            title = f'{endpoint_name} (Component {self.componemt_name}) OK'
        else:
            title = f'{endpoint_name} (Component {self.componemt_name}) unavailable'
        for webhook in webhooks:
            webhook_request = webhook.push_incident(
                incident_title, self.message)
            if webhook_request.ok:
                if logger is not None:
                    logger.info(f"Webhook {webhook.url} triggered with {title}")
            else:
                if logger is not None:
                    logger.warning(f"Webhook {webhook.url} failed with status [{webhook_request.status_code}]")

    def push_incident(self, client, public_incidents, endpoint_name, incident_title, webhooks, logger=None):
        """If the component status has changed, we create a new incident (if this is the first time it becomes unstable)
        or updates the existing incident once it becomes healthy again.
        """
        if self.component_id is None:
            return

        # # Only push incident for independent expectation
        # if not self.is_independent():
        #     return

        if not self.trigger_update:
            return

        if hasattr(self, "incident_id") and self.component_status == st.ComponentStatus.OPERATIONAL:
            incident_request = client.push_incident(
                self.component_status,
                public_incidents,
                self.component_id,
                incident_title,
                previous_incident_id=self.incident_id,
            )

            if incident_request.ok:
                # Successful metrics upload
                if logger is not None:
                    logger.info(
                        f'Incident updated, API healthy again: component status [{self.component_status}], message: "{self.message}"'
                    )
                del self.incident_id
            else:
                if logger is not None:
                    logger.warning(
                        f'Incident update failed with status [{incident_request.status_code}], message: "{self.message}"'
                    )

            self.trigger_webhooks(endpoint_name, incident_title, webhooks)
        elif not hasattr(self, "incident_id") and self.component_status != st.ComponentStatus.OPERATIONAL:
            incident_request = client.push_incident(
                self.component_status, public_incidents, self.component_id, incident_title, message=self.message
            )
            if incident_request.ok:
                # Successful incident upload.
                self.incident_id = incident_request.json()["data"]["id"]
                if logger is not None:
                    logger.info(
                        f'Incident uploaded, API unhealthy: component status [{self.component_status}], message: "{self.message}"'
                    )
            else:
                if logger is not None:
                    logger.warning(
                        f'Incident upload failed with status [{incident_request.status_code}], message: "{self.message}"'
                    )

            self.trigger_webhooks(endpoint_name, incident_title, webhooks)

    @abc.abstractmethod
    def get_status(self, response) -> ComponentStatus:
        """Returns the status of the API, following cachet's component status
        documentation: https://docs.cachethq.io/docs/component-statuses
        """

    @abc.abstractmethod
    def get_message(self, response) -> str:
        """Gets the error message."""

    @abc.abstractmethod
    def get_default_incident(self):
        """Returns the default status when this incident happens."""

    def parse_incident_status(self, configuration) -> ComponentStatus:
        return st.INCIDENT_MAP.get(configuration.get("incident", None), self.get_default_incident())


class HttpStatus(Expectation):

    def __init__(self, configuration, client):
        self.status_range = HttpStatus.parse_range(
            configuration["status_range"])
        super(HttpStatus, self).__init__(configuration, client)

    @staticmethod
    def parse_range(range_string):
        if isinstance(range_string, int):
            # This happens when there's no range and no dash character, it will
            # be parsed as int already.
            return range_string, range_string + 1

        statuses = range_string.split("-")
        if len(statuses) == 1:
            # When there was no range given, we should treat the first number
            # as a single status check.
            return int(statuses[0]), int(statuses[0]) + 1
        else:
            # We shouldn't look into more than one value, as this is a range
            # value.
            return int(statuses[0]), int(statuses[1])

    def get_status(self, response) -> ComponentStatus:
        if self.status_range[0] <= response.status_code < self.status_range[1]:
            return st.ComponentStatus.OPERATIONAL
        else:
            return self.incident_status

    def get_default_incident(self):
        return st.ComponentStatus.PARTIAL_OUTAGE

    def get_message(self, response):
        return f"Unexpected HTTP status ({response.status_code})"

    def __str__(self):
        return repr(f"HTTP status range: [{self.status_range[0]}, {self.status_range[1]}[")


class Latency(Expectation):

    def __init__(self, configuration, client):
        self.threshold = configuration["threshold"]
        super(Latency, self).__init__(configuration, client)

    def get_status(self, response) -> ComponentStatus:
        if response.elapsed.total_seconds() <= self.threshold:
            return st.ComponentStatus.OPERATIONAL
        else:
            return self.incident_status

    def get_default_incident(self):
        return st.ComponentStatus.PERFORMANCE_ISSUES

    def get_message(self, response):
        return "Latency above threshold: %.4f seconds" % (response.elapsed.total_seconds(),)

    def __str__(self):
        return repr("Latency threshold: %.4f seconds" % (self.threshold,))


class Regex(Expectation):

    def __init__(self, configuration, client):
        self.regex_string = configuration["regex"]
        self.regex = re.compile(configuration["regex"], re.UNICODE + re.DOTALL)
        super(Regex, self).__init__(configuration, client)

    def get_status(self, response) -> ComponentStatus:
        if self.regex.match(response.text):
            return st.ComponentStatus.OPERATIONAL
        else:
            return self.incident_status

    def get_default_incident(self):
        return st.ComponentStatus.PARTIAL_OUTAGE

    def get_message(self, response):
        return "Regex did not match anything in the body"

    def __str__(self):
        return repr(f"Regex: {self.regex_string}")


class JsonFieldValueCheck(Expectation):

    def __init__(self, configuration, client):
        self.field = configuration["field"]
        self.value = configuration["value"]
        self.all_fields = self.field.split(".")
        super(JsonFieldValueCheck, self).__init__(configuration, client)

    def get_status(self, response) -> ComponentStatus:
        try:
            respJson = json.loads(response.text)
            tmpDict = respJson
            for i in range(len(self.all_fields) - 1):
                tmpDict = tmpDict.get(self.all_fields[i], {})
            value = tmpDict.get(self.all_fields[-1])
            if value == self.value:
                return st.ComponentStatus.OPERATIONAL
            else:
                return self.incident_status
        except:
            return self.incident_status

    def get_default_incident(self):
        return st.ComponentStatus.PARTIAL_OUTAGE

    def get_message(self, response):
        return f"No matching of field {self.field} to value {self.value} in the body"

    def __str__(self):
        return repr(f"Json Check for (Field: {self.field}, Value: {self.value})")
