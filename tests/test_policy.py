"""Unit tests for egress-proxy/policy.py pure functions."""

import pytest


class TestIsPrivateHost:
    @pytest.mark.parametrize(
        "host",
        [
            "10.0.0.1",
            "10.255.255.255",
            "172.16.0.1",
            "172.31.255.255",
            "192.168.1.1",
            "192.168.0.0",
            "127.0.0.1",
            "169.254.169.254",
        ],
        ids=[
            "rfc1918-10",
            "rfc1918-10-high",
            "rfc1918-172",
            "rfc1918-172-high",
            "rfc1918-192",
            "rfc1918-192-low",
            "loopback",
            "link-local-metadata",
        ],
    )
    def test_private_ipv4_detected(self, policy, host):
        assert policy._is_private_host(host) is True

    @pytest.mark.parametrize(
        "host",
        ["::1", "fe80::1"],
        ids=["loopback-v6", "link-local-v6"],
    )
    def test_private_ipv6_detected(self, policy, host):
        assert policy._is_private_host(host) is True

    @pytest.mark.parametrize(
        "host",
        ["8.8.8.8", "1.1.1.1", "93.184.216.34"],
        ids=["google-dns", "cloudflare", "example-com"],
    )
    def test_public_ipv4_allowed(self, policy, host):
        assert policy._is_private_host(host) is False

    def test_public_ipv6_allowed(self, policy):
        assert policy._is_private_host("2001:4860:4860::8888") is False

    def test_unresolvable_hostname_no_crash(self, policy):
        assert policy._is_private_host("definitely-not-a-real-host.invalid") is False


class TestIsTrusted:
    @pytest.mark.parametrize(
        "host",
        [
            "github.com",
            "api.github.com",
            "gitlab.com",
            "api.anthropic.com",
            "proxy.golang.org",
            "sum.golang.org",
            "oauth2.googleapis.com",
        ],
    )
    def test_exact_match(self, policy, host):
        assert policy._is_trusted(host) is True

    def test_suffix_match(self, policy):
        assert policy._is_trusted("us-east5-aiplatform.googleapis.com") is True

    def test_another_suffix(self, policy):
        assert policy._is_trusted("europe-west4-aiplatform.googleapis.com") is True

    def test_port_stripped(self, policy):
        assert policy._is_trusted("github.com:443") is True

    def test_case_insensitive(self, policy):
        assert policy._is_trusted("GitHub.COM") is True

    def test_untrusted_host(self, policy):
        assert policy._is_trusted("evil.example.com") is False

    def test_untrusted_with_port(self, policy):
        assert policy._is_trusted("evil.example.com:443") is False


class TestTrustedHostsSet:
    def test_default_includes_package_hosts(self, policy):
        assert "proxy.golang.org" in policy.TRUSTED_HOSTS
        assert "sum.golang.org" in policy.TRUSTED_HOSTS
        assert "registry.npmjs.org" in policy.TRUSTED_HOSTS

    def test_offline_profile_removes_package_hosts(self, policy_offline):
        assert "proxy.golang.org" not in policy_offline.TRUSTED_HOSTS
        assert "sum.golang.org" not in policy_offline.TRUSTED_HOSTS
        assert "goproxy.io" not in policy_offline.TRUSTED_HOSTS
        assert "registry.npmjs.org" not in policy_offline.TRUSTED_HOSTS

    def test_offline_profile_keeps_core_hosts(self, policy_offline):
        assert "github.com" in policy_offline.TRUSTED_HOSTS
        assert "api.anthropic.com" in policy_offline.TRUSTED_HOSTS
        assert "gitlab.com" in policy_offline.TRUSTED_HOSTS


class TestAgentEgress:
    def test_agent_egress_adds_single_domain(self, policy_with_agent_egress):
        """AGENT_EGRESS='example.com' should add example.com to trusted hosts."""
        assert "example.com" in policy_with_agent_egress.TRUSTED_HOSTS
        assert "github.com" in policy_with_agent_egress.TRUSTED_HOSTS

    def test_agent_egress_adds_multiple_domains(self, policy_with_multiple_egress):
        """AGENT_EGRESS='a.com,b.net,c.org' should add all three domains."""
        assert "a.com" in policy_with_multiple_egress.TRUSTED_HOSTS
        assert "b.net" in policy_with_multiple_egress.TRUSTED_HOSTS
        assert "c.org" in policy_with_multiple_egress.TRUSTED_HOSTS
        assert "github.com" in policy_with_multiple_egress.TRUSTED_HOSTS

    def test_agent_egress_handles_spaces(self, policy_with_spaced_egress):
        """AGENT_EGRESS=' a.com , b.com ' should strip whitespace."""
        assert "a.com" in policy_with_spaced_egress.TRUSTED_HOSTS
        assert "b.com" in policy_with_spaced_egress.TRUSTED_HOSTS

    def test_agent_egress_empty_string_no_crash(self, policy_with_empty_egress):
        """AGENT_EGRESS='' should not crash or add empty strings."""
        assert "" not in policy_with_empty_egress.TRUSTED_HOSTS
        assert "github.com" in policy_with_empty_egress.TRUSTED_HOSTS

    def test_agent_egress_override_replaces_all(self, policy_with_override):
        """AGENT_EGRESS_OVERRIDE='custom.com' should replace entire TRUSTED_HOSTS."""
        assert "custom.com" in policy_with_override.TRUSTED_HOSTS
        assert "github.com" not in policy_with_override.TRUSTED_HOSTS
        assert "api.anthropic.com" not in policy_with_override.TRUSTED_HOSTS
        assert len(policy_with_override.TRUSTED_HOSTS) == 1

    def test_agent_egress_override_multiple_domains(self, policy_with_override_multiple):
        """AGENT_EGRESS_OVERRIDE with multiple domains replaces all defaults."""
        assert "x.com" in policy_with_override_multiple.TRUSTED_HOSTS
        assert "y.net" in policy_with_override_multiple.TRUSTED_HOSTS
        assert "z.org" in policy_with_override_multiple.TRUSTED_HOSTS
        assert "github.com" not in policy_with_override_multiple.TRUSTED_HOSTS
        assert len(policy_with_override_multiple.TRUSTED_HOSTS) == 3

    def test_mutual_exclusivity_override_wins(self, policy_with_both_vars):
        """When both vars set, AGENT_EGRESS_OVERRIDE takes precedence."""
        assert "override.com" in policy_with_both_vars.TRUSTED_HOSTS
        assert "ignored.com" not in policy_with_both_vars.TRUSTED_HOSTS
        assert "github.com" not in policy_with_both_vars.TRUSTED_HOSTS
