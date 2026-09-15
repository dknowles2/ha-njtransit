"""Errors raised by the NJ Transit client.

These are deliberately distinct because callers treat them differently: a
connection error is worth retrying, a malformed request never is, and a
GraphQL error usually means the endpoint changed under us.
"""

from __future__ import annotations


class NJTransitError(Exception):
    """Base class for every error this client raises."""


class NJTransitConnectionError(NJTransitError):
    """The endpoint could not be reached, or returned a transport-level error.

    Retryable. Entities should go unavailable rather than reporting stale
    data.
    """


class NJTransitRequestError(NJTransitError):
    """The request was rejected before it reached GraphQL.

    The WAF in front of the endpoint answers inline arguments with
    ``{"status": 400, "message": "Malformed request"}``, which is not
    GraphQL-shaped. **Not retryable** -- it means this client built a bad
    request, so retrying sends the same bad request again.
    """


class NJTransitAPIError(NJTransitError):
    """GraphQL returned errors.

    Usually schema drift: a field was renamed or stopped being populated.
    Retrying will not help until the query is updated.
    """


class NJTransitNotFoundError(NJTransitError):
    """The query succeeded but the payload was null.

    How an unknown station reports. Note the trip planner conflates this with
    a genuine no-service result -- both surface as "unable to find trips" --
    so a caller cannot distinguish a typo from an unserved pair.
    """


class NJTransitAuthError(NJTransitError):
    """The RailData API rejected the credentials.

    **Not retryable.** ``getToken`` answered ``Authenticated: False``, which
    means the username or password is wrong or the account is not enabled for
    production. Retrying spends one of the ten daily token requests on the
    same answer, so callers must stop and ask for new credentials instead.
    """


class NJTransitQuotaError(NJTransitError):
    """The RailData API refused a request on its daily usage limit.

    Retryable, but not soon: the counters reset at midnight Eastern. Callers
    should keep whatever they already have rather than poll harder.
    """
