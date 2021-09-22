import uuid
from typing import Optional

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.http.request import HttpRequest
from django.test import RequestFactory

from visitors.middleware import VisitorRequestMiddleware, VisitorSessionMiddleware
from visitors.models import Visitor
from visitors.settings import VISITOR_SESSION_KEY


@pytest.fixture
def visitor() -> Visitor:
    return Visitor.objects.create(email="fred@example.com", scope="foo", sessions_left=1)


class Session(dict):
    """Fake Session model used to support `session_key` property."""

    @property
    def session_key(self) -> str:
        return "foobar"

    def set_expiry(self, expiry: int) -> None:
        self.expiry = expiry


class TestVisitorMiddlewareBase:
    def request(self, url: str, user: Optional[User] = None) -> HttpRequest:
        factory = RequestFactory()
        request = factory.get(url)
        request.user = user or AnonymousUser()
        request.session = Session()
        return request


@pytest.mark.django_db
class TestVisitorRequestMiddleware(TestVisitorMiddlewareBase):
    def test_no_token(self) -> None:
        request = self.request("/", AnonymousUser())
        middleware = VisitorRequestMiddleware(lambda r: r)
        middleware(request)
        assert not request.user.is_visitor
        assert not request.visitor

    def test_token_does_not_exist(self) -> None:
        request = self.request(f"/?vuid={uuid.uuid4()}")
        middleware = VisitorRequestMiddleware(lambda r: r)
        middleware(request)
        assert not request.user.is_visitor
        assert not request.visitor

    def test_token_is_invalid(self, visitor: Visitor) -> None:
        visitor.deactivate()
        request = self.request(visitor.tokenise("/"))
        middleware = VisitorRequestMiddleware(lambda r: r)
        middleware(request)
        assert not request.user.is_visitor
        assert not request.visitor

    def test_valid_token(self, visitor: Visitor) -> None:
        request = self.request(visitor.tokenise("/"))
        middleware = VisitorRequestMiddleware(lambda r: r)
        middleware(request)
        assert request.user.is_visitor
        assert request.visitor == visitor


@pytest.mark.django_db
class TestVisitorSessionMiddleware(TestVisitorMiddlewareBase):
    def request(
        self,
        url: str,
        user: Optional[User] = None,
        is_visitor: bool = False,
        visitor: Visitor = None,
    ) -> HttpRequest:
        request = super().request(url, user)
        request.user.is_visitor = is_visitor
        request.visitor = visitor
        return request

    def test_visitor(self, visitor: Visitor) -> None:
        """Check that request.visitor is stashed in session."""
        request = self.request("/", is_visitor=True, visitor=visitor)
        assert not request.session.get(VISITOR_SESSION_KEY)
        middleware = VisitorSessionMiddleware(lambda r: r)
        middleware(request)
        assert request.session[VISITOR_SESSION_KEY] == visitor.session_data

    def test_no_visitor_no_session(self) -> None:
        """Check that no visitor on request or session passes."""
        request = self.request("/", is_visitor=False, visitor=None)
        middleware = VisitorSessionMiddleware(lambda r: r)
        middleware(request)
        assert not request.user.is_visitor
        assert not request.visitor

    def test_visitor_in_session(self, visitor: Visitor) -> None:
        """Check no visitor on request, but in session."""
        request = self.request("/", is_visitor=False, visitor=None)
        request.session[VISITOR_SESSION_KEY] = visitor.session_data
        middleware = VisitorSessionMiddleware(lambda r: r)
        middleware(request)
        assert request.user.is_visitor
        assert request.visitor == visitor

    def test_visitor_does_not_exist(self) -> None:
        """Check non-existant visitor in session."""
        request = self.request("/", is_visitor=False, visitor=None)
        request.session[VISITOR_SESSION_KEY] = str(uuid.uuid4())
        middleware = VisitorSessionMiddleware(lambda r: r)
        middleware(request)
        assert not request.user.is_visitor
        assert not request.visitor
        assert not request.session.get(VISITOR_SESSION_KEY)

    def test_sessions_left_reduces(self, visitor: Visitor) -> None:
        """ The amount of sessions_left should reduce when the 
        visitor accesses the site """
        initial_sessions = visitor.sessions_left
        request = self.request("/", is_visitor=True, visitor=visitor)
        request.session[VISITOR_SESSION_KEY] = str(uuid.uuid4())
        middleware = VisitorSessionMiddleware(lambda r: r)
        middleware(request)
        visitor.refresh_from_db()
        assert visitor.sessions_left < initial_sessions

    def test_zero_sessions_left_no_access(self, visitor: Visitor) -> None:
        """ When sessions_left == 0, the visitor should not have access """
        initial_sessions = visitor.sessions_left
        
        # visit 1 - with sessions_left == 1
        request = self.request("/", is_visitor=True, visitor=visitor)
        request.session[VISITOR_SESSION_KEY] = str(uuid.uuid4())
        middleware = VisitorSessionMiddleware(lambda r: r)
        middleware(request)
        assert request.visitor
        
        # visit 2 - with sessions_left == 0
        visitor.refresh_from_db()
        request = self.request("/", is_visitor=True, visitor=visitor)
        request.session[VISITOR_SESSION_KEY] = str(uuid.uuid4())
        middleware = VisitorSessionMiddleware(lambda r: r)
        middleware(request)
        assert not request.visitor