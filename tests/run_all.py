"""Run every offline test. Run: python -m tests.run_all"""
import sys

from tests import (test_benchmark, test_criteria, test_evidence, test_hardening, test_list_models, test_llm_failover,
                   test_offline, test_provenance, test_run_topic, test_scout, test_sources, test_steps, test_verify)

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print "→" in the default code page
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test_llm_failover.main()
    print()
    test_offline.main()
    print()
    test_criteria.main()
    print()
    test_verify.main()
    print()
    test_sources.main()
    print()
    test_evidence.main()
    print()
    test_benchmark.main()
    print()
    test_provenance.main()
    print()
    test_scout.main()
    print()
    test_steps.main()
    print()
    test_hardening.main()
    print()
    test_run_topic.main()
    print()
    test_list_models.main()
