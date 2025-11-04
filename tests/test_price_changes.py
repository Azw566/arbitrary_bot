"""
Test pour observer les changements de prix en temps réel
Lance 15 itérations avec un intervalle de 2 secondes
"""

from onchainprice import monitor_pools_continuously, POPULAR_POOLS

if __name__ == "__main__":
    pool_addresses = list(POPULAR_POOLS.values())
    
    print("\n📊 TEST DES VARIATIONS DE PRIX EN TEMPS RÉEL")
    print("=" * 100)
    print("Ce test va:")
    print("  1. Afficher les prix avec 8 décimales (haute précision)")
    print("  2. Montrer les variations par rapport à l'itération précédente")
    print("  3. Afficher le numéro de bloc Ethereum")
    print("  4. Détecter même les micro-variations")
    print("=" * 100)
    print("\nNote: Les prix peuvent être stables si le marché est calme.")
    print("      Les changements sont visibles principalement lors des swaps.")
    print("=" * 100)
    
    # Lancer le monitoring avec 15 itérations et 2s d'intervalle
    monitor_pools_continuously(pool_addresses, interval_seconds=2, max_iterations=15)
    
    print("\n✅ TEST TERMINÉ")
    print("\nObservations possibles:")
    print("  - Si les prix ne changent pas: le marché est stable entre les itérations")
    print("  - Si le bloc change mais pas les prix: pas de swaps sur ces pools")
    print("  - Les pools V3 à 0.05% sont généralement plus actifs")
    print("  - Les variations sont plus fréquentes pendant les heures de forte activité (US/EU)")
