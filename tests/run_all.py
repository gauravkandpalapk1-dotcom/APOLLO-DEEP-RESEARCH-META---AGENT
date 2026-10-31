"""Run every offline test. Run: python -m tests.run_all"""
from tests import test_llm_failover, test_offline

if __name__ == "__main__":
    test_llm_failover.main()
    print()
    test_offline.main()
