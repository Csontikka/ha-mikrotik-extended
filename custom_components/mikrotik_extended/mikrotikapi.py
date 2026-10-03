"""Mikrotik API for MikroTik Extended."""

import contextlib
import logging
import re
import ssl
from threading import Lock
from time import sleep, time

import librouteros
import librouteros.login as _rlogin
from librouteros.exceptions import MultiTrapError, TrapError
from librouteros.query import Key, Or
from voluptuous import Optional

from .const import (
    DEFAULT_ENCODING,
    DEFAULT_LOGIN_METHOD,
)

_LOGGER = logging.getLogger(__name__)

SCRIPT_ENVIRONMENT_PATH = "/system/script/environment"

# librouteros expects a *callable* for its ``login_method`` argument (singular).
# Map the configured method name (see DEFAULT_LOGIN_METHOD) to that callable so a
# plain config string is never handed straight to librouteros.
_LOGIN_METHODS = {"plain": _rlogin.plain}
if hasattr(_rlogin, "token"):  # legacy pre-6.43 challenge login
    _LOGIN_METHODS["token"] = _rlogin.token


# ---------------------------
#   MikrotikAPI
# ---------------------------
class MikrotikAPI:
    """Handle all communication with the Mikrotik API."""

    def __init__(
        self,
        host,
        username,
        password,
        port=0,
        use_ssl=True,
        ssl_verify=True,
        login_method=DEFAULT_LOGIN_METHOD,
        encoding=DEFAULT_ENCODING,
    ):
        """Initialize the Mikrotik Client."""
        self._host = host
        self._use_ssl = use_ssl
        self._ssl_verify = ssl_verify
        self._port = port
        self._username = username
        self._password = password
        self._login_method = login_method
        self._encoding = encoding
        self._ssl_wrapper = None
        self._refused_paths = set()
        self.lock = Lock()

        self._connection = None
        self._connected = False
        self._reconnected = True
        self._connection_epoch = 0
        self._connection_retry_sec = 58
        self.error = None
        self.connection_error_reported = True  # suppress first disconnect after startup
        self._first_connect = True
        self.disable_health = False

        # Default ports
        if not self._port:
            self._port = 8729 if self._use_ssl else 8728

    # ---------------------------
    #   has_reconnected
    # ---------------------------
    def has_reconnected(self) -> bool:
        """Check if mikrotik has reconnected"""
        if self._reconnected:
            self._reconnected = False
            return True

        return False

    # ---------------------------
    #   connection_check
    # ---------------------------
    def connection_check(self) -> bool:
        """Check if mikrotik is connected"""
        if not self._connected or not self._connection:
            if self._connection_epoch > time() - self._connection_retry_sec:
                return False

            if not self.connect():
                return False

        return True

    # ---------------------------
    #   close
    # ---------------------------
    def close(self):
        """Gracefully close the API connection without logging errors."""
        self.connection_error_reported = True
        if self._connection:
            with contextlib.suppress(Exception):
                self._connection.close()
        self._reconnected = False
        self._connected = False
        self._connection = None
        self._connection_epoch = 0

    # ---------------------------
    #   disconnect
    # ---------------------------
    def disconnect(self, location="unknown", error=None):
        """Disconnect from Mikrotik device."""
        if not error:
            error = "unknown"

        if not self.connection_error_reported:
            if location == "unknown":
                _LOGGER.debug("Mikrotik %s connection closed", self._host)
            else:
                _LOGGER.warning("Mikrotik %s error while %s : %s", self._host, location, error)

            self.connection_error_reported = True

        self._reconnected = False
        self._connected = False
        self._connection = None
        self._connection_epoch = 0

    # ---------------------------
    #   connect
    # ---------------------------
    def connect(self) -> bool:
        """Connect to Mikrotik device."""
        self.error = ""
        self._connected = False
        self._connection_epoch = time()

        kwargs = {
            "encoding": self._encoding,
            "port": self._port,
        }
        # librouteros wants the callable under ``login_method`` (singular). The
        # old ``login_methods`` key was silently ignored by librouteros <4 (which
        # accepted **kwargs) but is rejected outright by librouteros >=4, whose
        # connect() is keyword-only. That rejection surfaced to the user as a
        # generic "cannot_connect". Home Assistant ships librouteros 4.x, so
        # resolve the configured method to a callable and pass it correctly.
        login_method = _LOGIN_METHODS.get(self._login_method)
        if login_method is not None:
            kwargs["login_method"] = login_method

        with self.lock:
            try:
                if self._use_ssl:
                    if self._ssl_wrapper is None:
                        ssl_context = ssl.create_default_context()  # NOSONAR
                        if self._ssl_verify:
                            ssl_context.verify_mode = ssl.CERT_REQUIRED
                            ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
                        else:
                            ssl_context.check_hostname = False  # NOSONAR
                            ssl_context.verify_mode = ssl.CERT_NONE  # NOSONAR
                        self._ssl_wrapper = ssl_context.wrap_socket
                    kwargs["ssl_wrapper"] = self._ssl_wrapper
                self._connection = librouteros.connect(self._host, self._username, self._password, **kwargs)
            except Exception as e:
                if not self.connection_error_reported:
                    _LOGGER.error("Mikrotik %s error while connecting: %s", self._host, e)
                    self.connection_error_reported = True

                self.error_to_strings(f"{e}")
                self._connection = None
                return False
            else:
                if self.connection_error_reported and not self._first_connect:
                    _LOGGER.warning("Mikrotik Reconnected to %s", self._host)
                self.connection_error_reported = False
                self._first_connect = False

                self._connected = True
                self._reconnected = True

        return self._connected

    # ---------------------------
    #   error_to_strings
    # ---------------------------
    def error_to_strings(self, error):
        """Translate error output to error string."""
        self.error = "cannot_connect"
        if error == "invalid user name or password (6)":
            self.error = "wrong_login"

        if "ALERT_HANDSHAKE_FAILURE" in error:
            self.error = "ssl_handshake_failure"

        if "CERTIFICATE_VERIFY_FAILED" in error:
            self.error = "ssl_verify_failure"

    # ---------------------------
    #   connected
    # ---------------------------
    def connected(self) -> bool:
        """Return connected boolean."""
        return self._connected

    # ---------------------------
    #   query
    # ---------------------------
    def _note_refusal(self, path, error, what=None, consequence="Data from this path will be missing, the rest keeps working.") -> None:
        """Report a refused path once, not on every poll."""
        if path in self._refused_paths:
            _LOGGER.debug("Mikrotik %s path %s refused again: %s", self._host, path, error)
            return
        self._refused_paths.add(path)
        _LOGGER.warning(
            "Mikrotik %s refused %s: %s. %s",
            self._host,
            what or f"query {path}",
            error,
            consequence,
        )

    def _write_failed(self, location, what, error) -> None:
        """Sort out an exception raised by a write.

        A trap is the router answering and refusing this one request, so the
        connection is fine. Tearing it down here cost the whole session for a
        single refused change: the update cycle running at the time was cut
        short, the next poll had to reconnect, and the reconnect re-read
        everything the slow block covers. Anything that is not a trap is a
        broken link and still disconnects.

        ``what`` is the phrase after the host name, and it has to say what
        actually happened: "refused X" only for a request the router turned
        down whole. A script that errors part way through has run that far,
        and a message that reads as "nothing happened" would invite pressing
        the button again.

        Unlike a refused read, a refused write is logged every time. A read
        repeats every cycle, a write is one action somebody took, and they
        need to see that it did not happen.
        """
        if isinstance(error, (TrapError, MultiTrapError)):
            _LOGGER.warning(
                "Mikrotik %s %s: %s. The connection stays up.",
                self._host,
                what,
                error,
            )
            return
        self.disconnect(location, error)

    def _materialize_list(self, response, path):
        """Convert the API generator into a list; returns (response, missing_sentinel).

        Returns ``(response, False)`` on success, ``(None, True)`` when the API path
        is unavailable on this RouterOS version, and ``(None, False)`` on a hard
        error (connection has already been torn down by the caller).
        """
        try:
            response = list(response)
            _LOGGER.debug("API query %s returned %d entries", path, len(response) if response else 0)
            _LOGGER.debug("API query %s raw response: %s", path, response)
            return response, False
        except Exception as e:
            if path == "/system/health" and "no such command prefix" in str(e):
                self.disable_health = True
                return None, True
            if "no such command prefix" in str(e):
                _LOGGER.debug("Mikrotik %s path %s not available: %s", self._host, path, e)
                return None, True
            if isinstance(e, (TrapError, MultiTrapError)):
                # A trap is the router answering and refusing this one request,
                # so the connection is fine. Tearing it down here skipped every
                # remaining step of the update cycle and took the whole router
                # unavailable over a single unreadable path.
                self._note_refusal(path, e)
                return None, True
            self.disconnect(f"building list for path {path}", e)
            return None, False

    # The outcome of one attempt at a read, so the caller can tell a lost
    # link, which is worth one immediate retry, from an answer it must accept.
    _LINK_LOST = object()

    def _read_once(self, path, command, args, return_list):
        """One attempt at a read; returns the rows, None, or _LINK_LOST."""
        with self.lock:
            try:
                _LOGGER.debug("API query: %s", path)
                response = self._connection.path(path)
            except Exception as e:
                self.disconnect("path", e)
                return self._LINK_LOST

            if response and return_list and not command:
                response, missing = self._materialize_list(response, path)
                if response is None:
                    # A missing menu or a refusal is an answer; anything else
                    # took the connection down.
                    return None if missing else self._LINK_LOST

            elif response and command:
                _LOGGER.debug("API query: %s, %s, %s", path, command, args)
                try:
                    response = list(response(command, **args))
                except (TrapError, MultiTrapError) as e:
                    # Same reasoning as above: the router refused the command,
                    # it did not drop the connection.
                    self._note_refusal(f"{path} {command}", e)
                    return None
                except Exception as e:
                    self.disconnect("path", e)
                    return self._LINK_LOST

        return response or None

    def _retry_after_lost_link(self, path) -> bool:
        """Reconnect at once after a read lost the link; True when the read may be repeated.

        A connection the router dropped, for being idle or over a brief
        network hiccup, used to cost the whole update cycle: every remaining
        step was skipped, the entities went unavailable, and the next poll
        reconnected half a minute later. One immediate attempt covers that.
        If it fails too, connect() has started the usual back-off, and the
        router is left alone until it passes. Only reads are repeated: a
        command run twice, a reboot or a script, is worse than an error.
        """
        if not self.connection_check():
            return False
        _LOGGER.info("Mikrotik %s reconnected within the same poll after losing the connection while reading %s", self._host, path)
        return True

    def query(self, path, command=None, args=None, return_list=True) -> Optional(list):
        """Retrieve data from Mikrotik API."""
        """Returns generator object, unless return_list passed as True"""
        if path == "/system/health" and self.disable_health:
            return None

        if args is None:
            args = {}

        if not self.connection_check():
            return None

        response = self._read_once(path, command, args, return_list)
        if response is self._LINK_LOST:
            if not self._retry_after_lost_link(path):
                return None
            response = self._read_once(path, command, args, return_list)
            if response is self._LINK_LOST:
                return None
        return response

    # ---------------------------
    #   query_where
    # ---------------------------
    def query_where(self, path, any_of: dict) -> list | None:
        """Rows of a menu that match any of the given field values.

        The router does the filtering, so a menu that can hold a very large
        table, the routing table of a BGP router for one, is never read in
        full. Returns an empty list when nothing matches and None when the
        query could not be made, which the caller must tell apart: no rows is
        an answer, a failed query is not.
        """
        if not any_of:
            raise ValueError("query_where needs at least one field to match")
        if not self.connection_check():
            return None

        conditions = [Key(field) == value for field, value in any_of.items()]
        condition = conditions[0] if len(conditions) == 1 else Or(*conditions)
        response = self._read_where_once(path, any_of, condition)
        if response is self._LINK_LOST:
            if not self._retry_after_lost_link(path):
                return None
            response = self._read_where_once(path, any_of, condition)
            if response is self._LINK_LOST:
                return None
        return response

    def _read_where_once(self, path, any_of, condition):
        """One attempt at a filtered read; the rows, None, or _LINK_LOST."""
        with self.lock:
            try:
                _LOGGER.debug("API query: %s where any of %s", path, any_of)
                response = self._connection.path(path).select().where(condition)
            except Exception as e:
                self.disconnect("path", e)
                return self._LINK_LOST

            response, missing = self._materialize_list(response, path)
            if response is None and not missing:
                return self._LINK_LOST
        return response

    # ---------------------------
    #   set_value
    # ---------------------------
    def set_value(self, path, param, value, mod_param, mod_value) -> bool:
        """Modify a parameter"""
        _LOGGER.debug(
            "Mikrotik %s set_value: path=%s param=%s value=%s mod_param=%s mod_value=%s",
            self._host,
            path,
            param,
            value,
            mod_param,
            mod_value,
        )
        entry_found = None

        if not self.connection_check():
            return False

        # The lookup can be refused on its own, and then the write never
        # started, so the phrase follows the phase.
        what = f"refused listing {path}"
        with self.lock:
            try:
                _LOGGER.debug("API query: %s", path)
                response = self._connection.path(path)
                if not response:
                    return False

                for tmp in response:
                    if param not in tmp:
                        continue

                    if tmp[param] != value:
                        continue

                    entry_found = tmp[".id"]

                if not entry_found:
                    _LOGGER.warning(
                        "Mikrotik %s set_value parameter %s with value %s not found",
                        self._host,
                        param,
                        value,
                    )
                    return False

                params = {".id": entry_found, mod_param: mod_value}
                # Spell a bool the way it goes over the wire and the way the
                # user sees it on the router, not as Python's True or False.
                shown = ("yes" if mod_value else "no") if isinstance(mod_value, bool) else mod_value
                what = f"refused {path} set {mod_param}={shown}"
                response.update(**params)
            except Exception as e:
                self._write_failed("set_value", what, e)
                return False

        return True

    # ---------------------------
    #   execute
    # ---------------------------
    def execute(self, path, command, param, value, attributes=None) -> bool:
        """Execute a command"""
        entry_found = None
        params = {}

        if not self.connection_check():
            return False

        what = f"refused listing {path}"
        with self.lock:
            try:
                _LOGGER.debug("API query: %s", path)
                response = self._connection.path(path)
                if not response:
                    return False

                if param:
                    for tmp in response:
                        if param not in tmp:
                            continue

                        if tmp[param] != value:
                            continue

                        entry_found = tmp[".id"]

                    if not entry_found:
                        _LOGGER.warning(
                            "Mikrotik %s Execute %s parameter %s with value %s not found",
                            self._host,
                            command,
                            param,
                            value,
                        )
                        return False

                    params = {".id": entry_found}

                if attributes:
                    params.update(attributes)

                what = f"refused {path} {command}"
                tuple(response(command, **params))
            except Exception as e:
                self._write_failed("execute", what, e)
                return False

        return True

    # ---------------------------
    #   wol
    # ---------------------------
    def wol(self, mac: str, interface: str | None = None) -> bool:
        """Send Wake-on-LAN magic packet via MikroTik /tool wol"""
        if not self.connection_check():
            return False

        args = {"mac": mac}
        if interface and interface != "unknown":
            args["interface"] = interface

        with self.lock:
            try:
                _LOGGER.debug(
                    "WoL: sending magic packet to %s via %s",
                    mac,
                    interface or "broadcast",
                )
                response = self._connection.path("/tool")
                tuple(response("wol", **args))
            except Exception as e:
                self._write_failed("wol", f"refused /tool wol {mac}", e)
                return False

        return True

    # ---------------------------
    #   run_script
    # ---------------------------
    def run_script(self, name) -> bool:
        """Run script"""
        entry_found = None
        if not self.connection_check():
            return False

        what = "refused listing /system/script"
        with self.lock:
            try:
                _LOGGER.debug("API query: %s", "/system/script")
                response = self._connection.path("/system/script")
                if not response:
                    return False

                for tmp in response:
                    if "name" not in tmp:
                        continue

                    if tmp["name"] != name:
                        continue

                    entry_found = tmp[".id"]

                if not entry_found:
                    _LOGGER.error("Mikrotik %s Script %s not found", self._host, name)
                    return False

                # A trap here is usually the script itself failing part way,
                # after the router accepted the run, so it is not a refusal.
                what = f"reported an error running script {name}"
                tuple(response("run", **{".id": entry_found}))
            except Exception as e:
                self._write_failed("run_script", what, e)
                return False

        return True

    # ---------------------------
    #   set_env_variable
    # ---------------------------
    def _find_env_entry_id(self, env, name):
        """Return the ``.id`` of the env variable named ``name`` or ``None``."""
        for e in list(env):
            if e.get("name") == name:
                return e[".id"]
        return None

    def _update_env_entry(self, env, name, entry_id, value) -> bool:
        try:
            env.update(**{".id": entry_id, "value": str(value)})
        except Exception as e:
            self._write_failed("set_env_variable", f"refused environment variable {name}", e)
            return False
        return True

    def _schedule_env_create(self, name, value, sched_name) -> bool:
        # Defense in depth: name is interpolated into the scheduler script
        # unquoted, so it must be a plain identifier. The service layer already
        # validates this, but guard here too in case of other callers.
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", str(name)):
            _LOGGER.error("Mikrotik %s refused to create env variable with unsafe name", self._host)
            return False
        # Escape backslash, double quote and the RouterOS command-substitution
        # dollar sign so the value cannot break out of its quoted context.
        escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")
        on_event = f':global {name} "{escaped}"; /system/scheduler/remove [find name={sched_name}]'
        try:
            sched = self._connection.path("/system/scheduler")
            tuple(sched("add", name=sched_name, **{"on-event": on_event, "interval": "1s"}))
        except Exception as e:
            self._write_failed("set_env_variable", f"refused the scheduler entry creating environment variable {name}", e)
            return False
        return True

    def _verify_env_created(self, name) -> bool | None:
        """Return True if env var ``name`` exists, False on API failure, None if absent."""
        try:
            env2 = self._connection.path(SCRIPT_ENVIRONMENT_PATH)
            for e in env2:
                if e.get("name") == name:
                    return True
        except Exception as e:
            self._write_failed("set_env_variable", f"refused the check of environment variable {name}", e)
            return False
        return None

    def _cleanup_scheduler(self, sched_name) -> None:
        try:
            sched2 = self._connection.path("/system/scheduler")
            for s in sched2:
                if s.get("name") == sched_name:
                    sched2.remove(s[".id"])
        except Exception:
            pass

    def set_env_variable(self, name, value) -> bool:
        """Create or update a RouterOS script environment variable."""
        if not self.connection_check():
            return False

        with self.lock:
            # Check if variable already exists
            try:
                env = self._connection.path(SCRIPT_ENVIRONMENT_PATH)
                entry_id = self._find_env_entry_id(env, name)
            except Exception as e:
                self._write_failed("set_env_variable", "refused listing the environment variables", e)
                return False

            if entry_id:
                return self._update_env_entry(env, name, entry_id, value)

            # Variable doesn't exist — create via one-shot scheduler
            sched_name = "_ha_env_set"
            if not self._schedule_env_create(name, value, sched_name):
                return False

        # Wait for scheduler to execute (runs within 1s)
        sleep(2)

        # Verify the variable was created
        with self.lock:
            verified = self._verify_env_created(name)
            if verified is True:
                return True
            if verified is False:
                return False
            # Clean up scheduler if it didn't self-delete
            self._cleanup_scheduler(sched_name)

        _LOGGER.error("Mikrotik %s env variable %s not created by scheduler", self._host, name)
        return False

    # ---------------------------
    #   remove_env_variable
    # ---------------------------
    def remove_env_variable(self, name) -> bool:
        """Remove a RouterOS script environment variable."""
        if not self.connection_check():
            return False

        with self.lock:
            try:
                env = self._connection.path(SCRIPT_ENVIRONMENT_PATH)
                entries = list(env)
                for e in entries:
                    if e.get("name") == name:
                        env.remove(e[".id"])
                        return True
            except Exception as e:
                self._write_failed("remove_env_variable", f"refused removing environment variable {name}", e)
                return False

        _LOGGER.warning("Mikrotik %s env variable %s not found", self._host, name)
        return False

    # ---------------------------
    #   arp_ping
    # ---------------------------
    def arp_ping(self, address, interface) -> bool:
        """Check arp ping response traffic stats"""
        if not self.connection_check():
            return False

        response = self.query("/ping", return_list=False)
        if response is None:
            return False

        args = {
            "arp-ping": "no",
            "interval": "100ms",
            "count": 3,
            "interface": interface,
            "address": address,
        }
        with self.lock:
            try:
                ping = list(response("/ping", **args))
            except (TrapError, MultiTrapError) as e:
                # The router answered: most often an interface name it no
                # longer knows, because the port was renamed after the host
                # was first seen. The host counts as unreachable and the
                # session stays. This runs for every host on every tracker
                # cycle, so the refusal is reported once per interface,
                # the way a refused read is, not once per ping.
                self._note_refusal(
                    f"/ping interface={interface}",
                    e,
                    what=f"the tracker ping on interface {interface}",
                    consequence="Hosts pinged through it count as unreachable until the interface name matches again.",
                )
                return False
            except Exception as e:
                self.disconnect("arp_ping", e)
                return False

        for tmp in ping:
            if "received" in tmp and tmp["received"] > 0:
                _LOGGER.debug("Ping host success: %s", args["address"])
                return True

        _LOGGER.debug("Ping host failure: %s", args["address"])
        return False
