#!/usr/bin/env python
import re
import unittest

import mock
import pytest

from cachet_url_monitor.expectation import HttpStatus, Regex, Latency, JsonFieldValueCheck
from cachet_url_monitor.status import ComponentStatus
from cachet_url_monitor.client import CachetClient

TOKEN: str = "token_123"
CACHET_URL: str = "http://foo.localhost"


class LatencyTest(unittest.TestCase):
    def setUp(self):
        self.client = CachetClient(CACHET_URL, TOKEN)
        self.expectation = Latency({"type": "LATENCY", "threshold": 1}, self.client)

    def test_init(self):
        assert self.expectation.threshold == 1

    def test_get_status_healthy(self):
        def total_seconds():
            return 0.1

        request = mock.Mock()
        elapsed = mock.Mock()
        request.elapsed = elapsed
        elapsed.total_seconds = total_seconds

        assert self.expectation.get_status(request) == ComponentStatus.OPERATIONAL

    def test_get_status_unhealthy(self):
        def total_seconds():
            return 2

        request = mock.Mock()
        elapsed = mock.Mock()
        request.elapsed = elapsed
        elapsed.total_seconds = total_seconds

        assert self.expectation.get_status(request) == ComponentStatus.PERFORMANCE_ISSUES

    def test_get_message(self):
        def total_seconds():
            return 0.1

        request = mock.Mock()
        elapsed = mock.Mock()
        request.elapsed = elapsed
        elapsed.total_seconds = total_seconds

        assert self.expectation.get_message(request) == ("Latency above " "threshold: 0.1000 seconds")


class HttpStatusTest(unittest.TestCase):
    def setUp(self):
        self.client = CachetClient(CACHET_URL, TOKEN)
        self.expectation = HttpStatus({"type": "HTTP_STATUS", "status_range": "200-300"}, self.client)

    def test_init(self):
        assert self.expectation.status_range == (200, 300)

    def test_init_with_one_status(self):
        """With only one value, we still expect a valid tuple"""
        self.expectation = HttpStatus({"type": "HTTP_STATUS", "status_range": "200"}, self.client)

        assert self.expectation.status_range == (200, 201)

    def test_init_with_invalid_number(self):
        """Invalid values should just fail with a ValueError, as we can't convert it to int."""
        with pytest.raises(ValueError):
            self.expectation = HttpStatus({"type": "HTTP_STATUS", "status_range": "foo"}, self.client)

    def test_get_status_healthy(self):
        request = mock.Mock()
        request.status_code = 200

        assert self.expectation.get_status(request) == ComponentStatus.OPERATIONAL

    def test_get_status_healthy_boundary(self):
        request = mock.Mock()
        request.status_code = 299

        assert self.expectation.get_status(request) == ComponentStatus.OPERATIONAL

    def test_get_status_unhealthy(self):
        request = mock.Mock()
        request.status_code = 400

        assert self.expectation.get_status(request) == ComponentStatus.PARTIAL_OUTAGE

    def test_get_status_unhealthy_boundary(self):
        request = mock.Mock()
        request.status_code = 300

        assert self.expectation.get_status(request) == ComponentStatus.PARTIAL_OUTAGE

    def test_get_message(self):
        request = mock.Mock()
        request.status_code = 400

        assert self.expectation.get_message(request) == ("Unexpected HTTP " "status (400)")


class RegexTest(unittest.TestCase):
    def setUp(self):
        self.client = CachetClient(CACHET_URL, TOKEN)
        self.expectation = Regex({"type": "REGEX", "regex": ".*(find stuff).*"}, self.client)

    def test_init(self):
        assert self.expectation.regex == re.compile(".*(find stuff).*", re.UNICODE + re.DOTALL)

    def test_get_status_healthy(self):
        request = mock.Mock()
        request.text = "We could find stuff\n in this body."

        assert self.expectation.get_status(request) == ComponentStatus.OPERATIONAL

    def test_get_status_unhealthy(self):
        request = mock.Mock()
        request.text = "We will not find it here"

        assert self.expectation.get_status(request) == ComponentStatus.PARTIAL_OUTAGE

    def test_get_message(self):
        request = mock.Mock()
        request.text = "We will not find it here"

        assert self.expectation.get_message(request) == ("Regex did not match " "anything in the body")


class JsonFieldValueCheckTest(unittest.TestCase):
    def setUp(self):
        self.client = CachetClient(CACHET_URL, TOKEN)
        self.expectation = JsonFieldValueCheck({"type": "JSON_CHECK", "field": "data.status", "value": "UP"}, self.client)

    def test_init(self):
        assert self.expectation.all_fields == ['data', 'status']
        assert self.expectation.field == "data.status"
        assert self.expectation.value == "UP"

    def test_get_status_healthy(self):
        request = mock.Mock()
        request.text = '{"data": {"status": "UP", "other": 3}}'

        assert self.expectation.get_status(request) == ComponentStatus.OPERATIONAL

    def test_get_status_unhealthy(self):
        request = mock.Mock()
        request.text = '{"data": {"status": "Down", "other": 3}}'

        assert self.expectation.get_status(request) == ComponentStatus.PARTIAL_OUTAGE

    def test_get_message(self):
        request = mock.Mock()
        request.text = '{"data":{"status":"Down", "other":3}}'

        assert self.expectation.get_message(request) == ("No matching of field data.status to value UP in the body")
