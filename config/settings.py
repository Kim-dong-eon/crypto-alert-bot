EXCHANGE_ID = 'bitget'

# 4개의 타겟 코인 (바이낸스 선물 .p 기준 -> CCXT에서는 :USDT 로 표기)
SYMBOLS = [
    'BTC/USDT:USDT', 
    'ETH/USDT:USDT', 
    'XRP/USDT:USDT', 
    'SOL/USDT:USDT'
]

STRATEGY_PAIRS = [
    ('1d', '2h'),
    ('4h', '15m'),
    ('1h', '5m')
]

# 보조지표 파라미터
RSI_PERIOD = 14
STOCH_LENGTH = 14
STOCH_RSI_K = 3
STOCH_RSI_D = 3

# 타점 기준치
OVERSOLD = 30   # 30 이하 (롱 조건)
OVERBOUGHT = 70 # 70 이상 (숏 조건)
