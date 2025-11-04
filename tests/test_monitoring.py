"""
Test du système de monitoring en continu
Lance 5 itérations avec un intervalle de 3 secondes
"""

from onchainprice import monitor_pools_continuously, POPULAR_POOLS

if __name__ == "__main__":
    pool_addresses = list(POPULAR_POOLS.values())
    
    print("\n TEST DU MONITORING CONTINU")
    print("=" * 80)
    print("Configuration:")
    print(f"  - Pools surveillés: {len(pool_addresses)}")
    print(f"  - Intervalle: 3 secondes")
    print(f"  - Itérations: 5")
    print("=" * 80)
    
    # Lancer le monitoring avec 5 itérations
    monitor_pools_continuously(pool_addresses, interval_seconds=3, max_iterations=5)
    
    print("\n TEST TERMINÉ")
