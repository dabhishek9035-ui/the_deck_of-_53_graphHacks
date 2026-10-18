def climb_stairs(n):
    a = 1
    b = 1
    for _ in range(n):
        a, b = b, a + b
    return a


def min_coins(coins, amount):
    inf = amount + 1
    dp = [0] + [inf] * amount
    for a in range(1, amount + 1):
        for c in coins:
            if c <= a and dp[a - c] + 1 < dp[a]:
                dp[a] = dp[a - c] + 1
    return dp[amount] if dp[amount] != inf else -1
