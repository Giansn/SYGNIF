"""
Extracted prediction code from:
  - BitVision     https://github.com/shobrook/BitVision  (MIT, commit 6345fca)
  - CryptoPredictions  https://github.com/alimohammadiamirhossein/CryptoPredictions (commit 6f6ee3d)

Reference only — not wired into Sygnif.
"""

# =============================================================================
# PART 1 — BitVision (binary next-day direction classifier)
# =============================================================================

# --- 1a. Model (services/engine/model.py) ---

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler


class BV_Model:
    def __init__(self, training_data, hyperopt=False):
        self.scaler = StandardScaler()
        self.scaler.fit(training_data.drop("Trend", axis=1))
        self.model = LogisticRegression(penalty="l1", tol=0.001, C=1000, max_iter=150)
        normalized = self.scaler.transform(training_data.drop("Trend", axis=1))
        self.model.fit(normalized, training_data["Trend"])

    def predict(self, vector):
        return self.model.predict(self.scaler.transform(vector.reshape(1, -1)))


# --- 1b. Transformers (services/engine/transformers.py) ---

import pandas as pd
import dateutil.parser as dp
from scipy.stats import boxcox
# from realtime_talib import Indicator   # upstream dep


def bv_calculate_indicators(ohlcv_df):
    """Compute TA indicators via realtime_talib (upstream uses Quandl Bitstamp OHLCV)."""
    from realtime_talib import Indicator

    ohlcv_df = ohlcv_df.drop(["Volume (BTC)", "Weighted Price"], axis=1)
    ohlcv_df.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
    temp = ohlcv_df.copy()
    unix_times = [int(dp.parse(temp.iloc[i]["Date"]).strftime('%s')) for i in range(temp.shape[0])]
    temp["Date"] = pd.Series(unix_times).values
    temp.columns = ["date", "open", "high", "low", "close", "volume"]
    temp = temp.iloc[::-1]

    rocr3  = Indicator(temp, "ROCR", 3).getHistorical()[::-1]
    rocr6  = Indicator(temp, "ROCR", 6).getHistorical()[::-1]
    atr    = Indicator(temp, "ATR", 14).getHistorical()[::-1]
    obv    = Indicator(temp, "OBV").getHistorical()[::-1]
    trix   = Indicator(temp, "TRIX", 20).getHistorical()[::-1]
    mom1   = Indicator(temp, "MOM", 1).getHistorical()[::-1]
    mom3   = Indicator(temp, "MOM", 3).getHistorical()[::-1]
    adx14  = Indicator(temp, "ADX", 14).getHistorical()[::-1]
    adx20  = Indicator(temp, "ADX", 20).getHistorical()[::-1]
    willr  = Indicator(temp, "WILLR", 14).getHistorical()[::-1]
    rsi6   = Indicator(temp, "RSI", 6).getHistorical()[::-1]
    rsi12  = Indicator(temp, "RSI", 12).getHistorical()[::-1]
    macd, macd_signal, macd_hist = Indicator(temp, "MACD", 12, 26, 9).getHistorical()
    macd, macd_signal, macd_hist = macd[::-1], macd_signal[::-1], macd_hist[::-1]
    ema6   = Indicator(temp, "MA", 6, 1).getHistorical()[::-1]
    ema12  = Indicator(temp, "MA", 12, 1).getHistorical()[::-1]

    n = min(len(mom1), len(mom3), len(adx14), len(adx20), len(willr),
            len(rsi6), len(rsi12), len(macd), len(macd_signal), len(macd_hist),
            len(ema6), len(ema12), len(rocr3), len(rocr6), len(atr), len(obv), len(trix))
    ohlcv_df = ohlcv_df[:n].drop(["Open", "High", "Low"], axis=1)
    for name, arr in [("MOM (1)", mom1), ("MOM (3)", mom3), ("ADX (14)", adx14),
                      ("ADX (20)", adx20), ("WILLR", willr), ("RSI (6)", rsi6),
                      ("RSI (12)", rsi12), ("MACD", macd), ("MACD (Signal)", macd_signal),
                      ("MACD (Historical)", macd_hist), ("EMA (6)", ema6), ("EMA (12)", ema12),
                      ("ROCR (3)", rocr3), ("ROCR (6)", rocr6), ("ATR (14)", atr),
                      ("OBV", obv), ("TRIX (20)", trix)]:
        ohlcv_df[name] = pd.Series(arr[:n]).values
    return ohlcv_df


def bv_merge_datasets(origin_df, other_sets):
    merged = origin_df
    for s in other_sets:
        merged = pd.merge(merged, s, on="Date")
    return merged


def bv_fix_null_vals(df):
    return df if not df.isnull().any().any() else df.fillna(method="ffill")


def bv_add_lag_vars(df, lag=3):
    new = {}
    for col in df.drop("Date", axis=1):
        new[col] = df[col]
        for l in range(1, lag + 1):
            new["%s_lag%d" % (col, l)] = df[col].shift(-l)
    out = pd.DataFrame(new, index=df.index)
    out["Date"] = df["Date"]
    return out.dropna()


def bv_power_transform(df):
    for h in df.drop("Date", axis=1).columns:
        if not any(df[h] < 0) and not any(df[h] == 0):
            df[h] = boxcox(df[h])[0]
    return df


def bv_binarize_labels(df):
    trends = [None]
    for idx in range(df.shape[0] - 1):
        diff = df.iloc[idx]["Close"] - df.iloc[idx + 1]["Close"]
        trends.append(-1 if diff < 0 else 1)
    df["Trend"] = pd.Series(trends).values
    return df


# --- 1c. Full prediction pipeline (services/trader.py :: make_prediction) ---

def bv_make_prediction(price_data, blockchain_data):
    """
    End-to-end BitVision prediction.
    Returns 1 (down/flat) or -1 (up) under their label convention.
    """
    processed = (
        price_data
        .pipe(bv_calculate_indicators)
        .pipe(bv_merge_datasets, other_sets=[blockchain_data])
        .pipe(bv_fix_null_vals)
        .pipe(bv_add_lag_vars)
        .pipe(bv_power_transform)
        .pipe(bv_binarize_labels)
        .drop("Date", axis=1)
    )
    feature_vector = processed.drop("Trend", axis=1).iloc[0]
    model = BV_Model(processed.drop(processed.index[0]), hyperopt=False)
    return model.predict(feature_vector.values)[0]


# =============================================================================
# PART 2 — CryptoPredictions (continuous price forecasting toolbox)
# =============================================================================

import numpy as np
import math
from math import fabs
from datetime import datetime

# --- 2a. Indicators (data_loader/indicators.py) — numba-JIT pure-numpy ---

from numba import jit


@jit(nopython=True)
def cp_convolve(data, kernel):
    n_d, n_k = len(data), len(kernel)
    n_o = n_d - n_k + 1
    out = np.array([np.nan] * n_o)
    kernel = np.flip(kernel)
    for i in range(n_o):
        w = data[i:i + n_k]
        out[i] = sum([w[j] * kernel[j] for j in range(n_k)])
    return out


@jit(nopython=True)
def cp_sma(data, period):
    size = len(data)
    out = np.array([np.nan] * size)
    for i in range(period - 1, size):
        out[i] = np.mean(data[i - period + 1:i + 1])
    return out


@jit(nopython=True)
def cp_wma(data, period):
    weights = np.arange(period, 0, -1)
    weights = weights / weights.sum()
    out = cp_convolve(data, weights)
    return np.concatenate((np.array([np.nan] * (len(data) - len(out))), out))


@jit(nopython=True)
def cp_ema(data, period, smoothing=2.0):
    size = len(data)
    w = smoothing / (period + 1)
    out = np.array([np.nan] * size)
    out[0] = data[0]
    for i in range(1, size):
        out[i] = (data[i] * w) + (out[i - 1] * (1 - w))
    out[:period - 1] = np.nan
    return out


@jit(nopython=True)
def cp_ewma(data, period, alpha=1.0):
    weights = (1 - alpha) ** np.arange(period)
    weights /= np.sum(weights)
    out = cp_convolve(data, weights)
    return np.concatenate((np.array([np.nan] * (len(data) - len(out))), out))


@jit(nopython=True)
def cp_trix(data, period, smoothing=2.0):
    return ((3 * cp_ema(data, period, smoothing)
             - 3 * cp_ema(cp_ema(data, period, smoothing), period, smoothing))
            + cp_ema(cp_ema(cp_ema(data, period, smoothing), period, smoothing), period, smoothing))


@jit(nopython=True)
def cp_macd(data, fast, slow, smoothing=2.0):
    return cp_ema(data, fast, smoothing) - cp_ema(data, slow, smoothing)


@jit(nopython=True)
def cp_stoch(c_close, c_high, c_low, period_k, period_d):
    size = len(c_close)
    k = np.array([np.nan] * size)
    for i in range(period_k - 1, size):
        e, s = i + 1, i + 1 - period_k
        ml = np.min(c_low[s:e])
        mh = np.max(c_high[s:e])
        if ml == mh:
            ml -= 0.1
        k[i] = ((c_close[i] - ml) / (mh - ml)) * 100
    return k, cp_sma(k, period_d)


@jit(nopython=True)
def cp_wpr(c_close, c_high, c_low, period):
    size = len(c_close)
    out = np.array([np.nan] * size)
    for i in range(period - 1, size):
        e, s = i + 1, i + 1 - period
        mh = np.max(c_high[s:e])
        out[i] = ((mh - c_close[i]) / (mh - np.min(c_low[s:e]))) * -100
    return out


@jit(nopython=True)
def cp_rsi(data, period, smoothing=2.0, f_sma=True, f_clip=True, f_abs=True):
    size = len(data)
    delta = np.diff(data)
    if f_clip:
        up = np.clip(delta, a_min=0, a_max=np.max(delta))
        down = np.clip(delta, a_min=np.min(delta), a_max=0)
    else:
        up, down = delta.copy(), delta.copy()
        up[delta < 0] = 0.0
        down[delta > 0] = 0.0
    if f_abs:
        for i, x in enumerate(down):
            down[i] = fabs(x)
    else:
        down = np.abs(down)
    rs = cp_sma(up, period) / cp_sma(down, period) if f_sma else cp_ema(up, period - 1, smoothing) / cp_ema(down, period - 1, smoothing)
    out = np.array([np.nan] * size)
    out[1:] = 100 - 100 / (1 + rs)
    return out


@jit(nopython=True)
def cp_srsi(data, period, smoothing=2.0, f_sma=True, f_clip=True, f_abs=True):
    r = cp_rsi(data, period, smoothing, f_sma, f_clip, f_abs)[period:]
    s = np.array([np.nan] * len(r))
    for i in range(period - 1, len(r)):
        w = r[i + 1 - period:i + 1]
        mw = np.min(w)
        s[i] = ((r[i] - mw) / (np.max(w) - mw)) * 100
    return np.concatenate((np.array([np.nan] * (len(data) - len(s))), s))


@jit(nopython=True)
def cp_bollinger_bands(data, period, dev_up=2.0, dev_down=2.0):
    size = len(data)
    bb_up = np.array([np.nan] * size)
    bb_down = np.array([np.nan] * size)
    bb_mid = cp_sma(data, period)
    for i in range(period - 1, size):
        std = np.std(data[i - period + 1:i + 1])
        bb_up[i] = bb_mid[i] + std * dev_up
        bb_down[i] = bb_mid[i] - std * dev_down
    return bb_mid, bb_up, bb_down


@jit(nopython=True)
def cp_tr(c_open, c_high, c_low):
    return np.maximum(np.maximum(c_open - c_low, np.abs(c_high - c_open)), np.abs(c_low - c_open))


@jit(nopython=True)
def cp_atr(c_open, c_high, c_low, period):
    return cp_sma(cp_tr(c_open, c_high, c_low), period)


@jit(nopython=True)
def cp_keltner_channel(c_close, c_open, c_high, c_low, period, smoothing=2.0):
    e = cp_ema(c_close, period, smoothing)
    aa = 2 * cp_atr(c_open, c_high, c_low, period)
    return e, e + aa, e - aa


@jit(nopython=True)
def cp_ichimoku(data, tenkansen=9, kinjunsen=26, senkou_b=52, shift=26):
    size = len(data)
    n_t = np.array([np.nan] * size)
    n_k = np.array([np.nan] * size)
    n_sb = np.array([np.nan] * (size + shift))
    for i in range(tenkansen - 1, size):
        w = data[i + 1 - tenkansen:i + 1]
        n_t[i] = (np.max(w) + np.min(w)) / 2
    for i in range(kinjunsen - 1, size):
        w = data[i + 1 - kinjunsen:i + 1]
        n_k[i] = (np.max(w) + np.min(w)) / 2
    for i in range(senkou_b - 1, size):
        w = data[i + 1 - senkou_b:i + 1]
        n_sb[i + shift] = (np.max(w) + np.min(w)) / 2
    chikou = np.concatenate((data[shift:], np.array([np.nan] * (size - shift))))
    senkou_a = np.concatenate((np.array([np.nan] * shift), (n_t + n_k) / 2))
    return n_t, n_k, chikou, senkou_a, n_sb


@jit(nopython=True)
def cp_momentum(data, period):
    size = len(data)
    out = np.array([np.nan] * size)
    for i in range(period - 1, size):
        out[i] = data[i] - data[i - period + 1]
    return out


@jit(nopython=True)
def cp_roc(data, period):
    size = len(data)
    out = np.array([np.nan] * size)
    for i in range(period - 1, size):
        p = data[i - period + 1]
        out[i] = ((data[i] - p) / p) * 100
    return out


@jit(nopython=True)
def cp_vix(c_close, c_low, period):
    size = len(c_close)
    out = np.array([np.nan] * size)
    for i in range(period - 1, size):
        hc = np.max(c_close[i + 1 - period:i + 1])
        out[i] = ((hc - c_low[i]) / hc) * 100
    return out


@jit(nopython=True)
def cp_chop(c_close, c_open, c_high, c_low, period=14):
    size = len(c_close)
    out = np.array([np.nan] * size)
    a = cp_atr(c_open, c_high, c_low, period)
    for i in range(period - 1, size):
        e, s = i + 1, i + 1 - period
        out[i] = (100 * np.log10(np.sum(a[s:e]) / (np.max(c_high[s:e]) - np.min(c_low[s:e])))) / np.log10(period)
    return out


@jit(nopython=True)
def cp_cog(data, period=10):
    size = len(data)
    out = np.array([np.nan] * size)
    for i in range(period - 1, size):
        w = data[i + 1 - period:i + 1]
        den = np.sum(w)
        num = 0
        for j in range(period):
            num += w[j] * (period - j)
        out[i] = -num / den
    return out


def cp_calculate_indicators(mean_, close_, open_, high_, low_, volume_):
    indicators = {
        'close': close_, 'open': open_, 'high': high_, 'low': low_, 'volume': volume_,
        'short_wma': cp_wma(mean_, 20), 'wma': cp_wma(mean_, 50), 'long_wma': cp_wma(mean_, 100),
        'ewma': cp_ewma(mean_, 15, 0.97), 'tema': cp_trix(mean_, 30),
        'macd': cp_macd(mean_, 12, 26),
    }
    s1, s2 = cp_stoch(close_, high_, low_, 5, 3)
    indicators['stoch1'], indicators['stoch2'] = s1, s2
    indicators['wpr'] = cp_wpr(close_, high_, low_, 14)
    indicators['rsi_experts'] = cp_rsi(mean_, 5)
    indicators['rsi'] = cp_rsi(mean_, 14)
    indicators['srsi'] = cp_srsi(mean_, 14)
    _, bu, bd = cp_bollinger_bands(mean_, 20)
    indicators['bolinger_up'], indicators['bolinger_down'] = bu, bd
    _, ku, kd = cp_keltner_channel(close_, open_, high_, low_, 20)
    indicators['kc_up'], indicators['kc_down'] = ku, kd
    t, k, ch, sa, sb = cp_ichimoku(mean_)
    indicators.update({'tenkansen': t, 'kinjunsen': k, 'chikou': ch, 'senkou_a': sa, 'senkou_b': sb})
    indicators['atr'] = cp_atr(open_, high_, low_, 14)
    indicators['momentum_'] = cp_momentum(mean_, 40)
    indicators['roc'] = cp_roc(mean_, 12)
    indicators['vix'] = cp_vix(close_, low_, 30)
    indicators['chop'] = cp_chop(close_, open_, high_, low_)
    indicators['cog'] = cp_cog(mean_)
    return indicators


def cp_add_indicators_to_dataset(indicators, indicators_names, dates, mean_):
    new_data = [indicators[name] for name in indicators_names]
    new_data.append(mean_)
    indicators_names.append('mean')
    arr = np.swapaxes(np.array(new_data), 0, 1)[100:]
    return arr, dates[100:]


# --- 2b. Dataset windowing (data_loader/creator.py) ---

def cp_create_dataset(dataset, dates, look_back, features):
    """Sliding-window builder: flattened past `look_back` bars → target = next mean."""
    data_x = []
    for i in range(len(dataset) - look_back - 1):
        a = dataset[i:(i + look_back), :].reshape(-1)
        d = datetime.strptime(str(dates[i]).split('+')[0].split('.')[0], '%Y-%m-%d %H:%M:%S')
        b = [d] + a.tolist()
        b.append(dataset[(i + look_back), :][-1])
        data_x.append(b)

    data_x = np.array(data_x)
    cols = ['Date']
    counter = counter_date = 0
    for i in range(data_x.shape[1] - 2):
        cols.append(f'{features[counter]}_day{counter_date}')
        counter += 1
        if counter >= len(features):
            counter = 0
            counter_date += 1
    cols.append('prediction')

    df = pd.DataFrame(data_x, columns=cols)
    last_col = [f'{features[i]}_day{counter_date-1}' for i in range(len(features))] + ['prediction']
    last_col.remove(f'High_day{counter_date-1}')
    last_col.remove(f'Low_day{counter_date-1}')
    last_col.remove(f'mean_day{counter_date-1}')
    profit_calc = df.copy()[[
        'Date', f'Low_day{counter_date-1}', f'High_day{counter_date-1}',
        f'close_day{counter_date-1}', f'open_day{counter_date-1}', f'volume_day{counter_date-1}'
    ]]
    df.drop(last_col, axis=1, inplace=True)
    df.rename(columns={
        f'High_day{counter_date-1}': 'predicted_high',
        f'Low_day{counter_date-1}': 'predicted_low',
        f'mean_day{counter_date-1}': 'prediction',
    }, inplace=True)
    profit_calc.rename(columns={
        f'High_day{counter_date-1}': 'High', f'Low_day{counter_date-1}': 'Low',
        f'open_day{counter_date-1}': 'Open', f'close_day{counter_date-1}': 'Close',
        f'volume_day{counter_date-1}': 'Volume',
    }, inplace=True)
    return df, profit_calc


def cp_preprocess(dataset, features, indicators_names, window_size, train_start, valid_end):
    """Full preprocess: filter dates → indicators → windowing."""
    dataset = dataset[(dataset['Date'] > train_start) & (dataset['Date'] < valid_end)]
    dates = dataset['Date']
    df = dataset[features].copy()
    if 'low' in df.columns: df.rename(columns={'low': 'Low'}, inplace=True)
    if 'high' in df.columns: df.rename(columns={'high': 'High'}, inplace=True)
    df['Mean'] = (df['Low'] + df['High']) / 2
    df.drop('Date', axis=1, inplace=True)
    df.dropna(inplace=True)
    arr = np.array(df.drop('Mean', axis=1))
    indicators = cp_calculate_indicators(
        mean_=np.array(df.Mean), low_=np.array(df.Low), high_=np.array(df.High),
        open_=np.array(df.open), close_=np.array(df.close), volume_=np.array(df.volume))
    ind_names = list(indicators_names)
    arr1, dates = cp_add_indicators_to_dataset(indicators, ind_names, dates, np.array(df.Mean))
    arr = np.concatenate((arr[100:], arr1), axis=1)
    feat = [f for f in features if f != 'Date'] + ind_names
    return cp_create_dataset(arr, list(dates), look_back=window_size, features=feat)


# --- 2c. Models ---

# RandomForest
from sklearn.ensemble import RandomForestRegressor

class CP_RandomForest:
    def __init__(self, n_estimators=1000, random_state=42):
        self.model = RandomForestRegressor(n_estimators=n_estimators, random_state=random_state)

    def fit(self, data_x):
        arr = np.array(data_x)
        self.model.fit(arr[:, 1:-1], arr[:, -1])

    def predict(self, test_x):
        return self.model.predict(np.array(test_x.iloc[:, 1:], dtype=float))


# LSTM
from keras.models import Sequential
from keras.layers import Dense, LSTM as KerasLSTM
from sklearn.preprocessing import MinMaxScaler

class CP_LSTM:
    def __init__(self, hidden_dim=256, epochs=50):
        self.model = Sequential()
        self.hidden_dim = hidden_dim
        self.epochs = epochs
        self._created = False
        self.sc_in = MinMaxScaler(feature_range=(0, 1))
        self.sc_out = MinMaxScaler(feature_range=(0, 1))

    def _build(self, n_features):
        self.model.add(KerasLSTM(self.hidden_dim, return_sequences=True, input_shape=(1, n_features)))
        self.model.add(KerasLSTM(self.hidden_dim))
        self.model.add(Dense(1))
        self.model.compile(loss='mean_squared_error', optimizer='adam')

    def fit(self, data_x):
        arr = np.array(data_x)
        X, y = arr[:, 1:-1], arr[:, -1]
        if not self._created:
            self._build(X.shape[1])
            self._created = True
        X = self.sc_in.fit_transform(X)
        y = self.sc_out.fit_transform(y.reshape(-1, 1))
        X = X.reshape(X.shape[0], 1, X.shape[1]).astype(float)
        self.model.fit(X, y.astype(float), epochs=self.epochs, verbose=1, shuffle=False, batch_size=50)

    def predict(self, test_x):
        X = np.array(test_x.iloc[:, 1:], dtype=float)
        X = self.sc_in.transform(X).reshape(X.shape[0], 1, X.shape[1])
        pred = self.model.predict(X).reshape(-1, 1)
        return self.sc_out.inverse_transform(pred)


# GRU
from keras.layers import GRU as KerasGRU

class CP_GRU:
    def __init__(self, hidden_dim=256, epochs=50):
        self.model = Sequential()
        self.hidden_dim = hidden_dim
        self.epochs = epochs
        self._created = False
        self.sc_in = MinMaxScaler(feature_range=(0, 1))
        self.sc_out = MinMaxScaler(feature_range=(0, 1))

    def _build(self, n_features):
        self.model.add(KerasGRU(self.hidden_dim, return_sequences=True, input_shape=(1, n_features)))
        self.model.add(KerasGRU(self.hidden_dim))
        self.model.add(Dense(1))
        self.model.compile(loss='mean_squared_error', optimizer='adam')

    def fit(self, data_x):
        arr = np.array(data_x)
        X, y = arr[:, 1:-1], arr[:, -1]
        if not self._created:
            self._build(X.shape[1])
            self._created = True
        X = self.sc_in.fit_transform(X)
        y = self.sc_out.fit_transform(y.reshape(-1, 1))
        X = X.reshape(X.shape[0], 1, X.shape[1]).astype(float)
        self.model.fit(X, y.astype(float), epochs=self.epochs, verbose=0, shuffle=False, batch_size=50)

    def predict(self, test_x):
        X = np.array(test_x.iloc[:, 1:], dtype=float)
        X = self.sc_in.transform(X).reshape(X.shape[0], 1, X.shape[1])
        pred = self.model.predict(X).reshape(-1, 1)
        return self.sc_out.inverse_transform(pred)


# XGBoost
import xgboost as xgb
from sklearn.model_selection import RandomizedSearchCV

class CP_XGBoost:
    def __init__(self, response_col='prediction', date_col='Date'):
        self.response_col = response_col
        self.date_col = date_col
        self.model = RandomizedSearchCV(
            xgb.XGBRegressor(),
            param_distributions={
                "learning_rate": [0.10, 0.20, 0.30],
                "max_depth": [1, 3, 4, 5, 6, 7],
                "n_estimators": [int(x) for x in np.linspace(100, 1000, 10)],
                "min_child_weight": list(range(3, 10)),
                "gamma": [0.0, 0.2, 0.4, 0.6],
                "subsample": [0.5, 0.6, 0.7, 0.8, 0.9, 1],
                "colsample_bytree": [0.5, 0.7, 0.9, 1],
            },
            n_iter=20, n_jobs=-1, cv=5, verbose=0,
        )
        self.regressors = []

    def fit(self, data_x):
        self.regressors = [c for c in data_x.columns if c not in (self.response_col, self.date_col)]
        self.model.fit(
            data_x[self.regressors].astype(float),
            data_x[self.response_col].astype(float),
        )

    def predict(self, test_x):
        return self.model.predict(test_x[self.regressors].astype(float))


# SARIMAX
from statsmodels.tsa.statespace.sarimax import SARIMAX

class CP_SARIMAX:
    def __init__(self, order=(1, 1, 1), seasonal_order=(1, 1, 0, 0)):
        self.order = order
        self.seasonal_order = seasonal_order
        self.sc_in = MinMaxScaler(feature_range=(0, 1))
        self.sc_out = MinMaxScaler(feature_range=(0, 1))
        self.train_size = -1

    def fit(self, data_x):
        arr = np.array(data_x)
        X, y = arr[:, 1:-1], arr[:, -1]
        self.train_size = X.shape[0]
        X = self.sc_in.fit_transform(X)
        y = self.sc_out.fit_transform(y.reshape(-1, 1)).astype(float)
        self.result = SARIMAX(
            y, exog=X.astype(float), order=self.order, seasonal_order=self.seasonal_order,
            enforce_invertibility=False, enforce_stationarity=False
        ).fit()

    def predict(self, test_x):
        X = self.sc_in.transform(np.array(test_x.iloc[:, 1:], dtype=float))
        n = X.shape[0]
        pred = self.result.predict(start=self.train_size, end=self.train_size + n - 1, exog=X)
        return self.sc_out.inverse_transform(pred.reshape(-1, 1))


# ARIMA
class CP_ARIMA:
    def __init__(self, order=(1, 1, 1)):
        self.order = order
        self.sc_in = MinMaxScaler(feature_range=(0, 1))
        self.sc_out = MinMaxScaler(feature_range=(0, 1))
        self.train_size = -1

    def fit(self, data_x):
        from statsmodels.tsa.arima_model import ARIMA
        arr = np.array(data_x)
        X, y = arr[:, 1:-1], arr[:, -1]
        self.train_size = X.shape[0]
        X = self.sc_in.fit_transform(X)
        y = self.sc_out.fit_transform(y.reshape(-1, 1)).astype(float)
        self.result = ARIMA(y, exog=X.astype(float), order=self.order).fit()

    def predict(self, test_x):
        X = self.sc_in.transform(np.array(test_x.iloc[:, 1:], dtype=float))
        n = X.shape[0]
        pred = self.result.predict(start=self.train_size, end=self.train_size + n - 1, exog=X)
        return self.sc_out.inverse_transform(pred.reshape(-1, 1))


# Prophet
class CP_Prophet:
    def __init__(self, response_col='prediction', date_col='Date'):
        self.response_col = response_col
        self.date_col = date_col
        self.regressors = []

    def fit(self, data_x):
        from prophet import Prophet
        self.model = Prophet()
        self.regressors = [c for c in data_x.columns if c not in (self.response_col, self.date_col)]
        for f in self.regressors:
            self.model.add_regressor(f)
        data_x[self.regressors] = data_x[self.regressors].astype(float)
        data_x[self.response_col] = data_x[self.response_col].astype(float)
        self.model.fit(data_x.reset_index().rename(columns={self.date_col: 'ds', self.response_col: 'y'}))

    def predict(self, test_x):
        test_x[self.regressors] = test_x[self.regressors].astype(float)
        pred = self.model.predict(test_x.reset_index().rename(columns={self.date_col: 'ds', self.response_col: 'y'}))
        return pred.yhat


# NeuralProphet
class CP_NeuralProphet:
    def __init__(self, response_col='prediction', date_col='Date', is_daily=True, is_hourly=False, confidence_level=0.8):
        self.response_col = response_col
        self.date_col = date_col
        self.is_daily = is_daily
        self.is_hourly = is_hourly
        self.quantile_list = [round((1 - confidence_level) / 2, 2),
                              round(confidence_level + (1 - confidence_level) / 2, 2)]
        self.regressors = []

    def fit(self, data_x):
        from neuralprophet import NeuralProphet
        n_lags = 2 * 30 if self.is_daily else 3 * 24 if self.is_hourly else 30
        self.model = NeuralProphet(
            yearly_seasonality=self.is_daily, weekly_seasonality=self.is_daily,
            daily_seasonality=self.is_hourly, n_lags=n_lags,
            learning_rate=0.003, quantiles=self.quantile_list,
        )
        self.regressors = [c for c in data_x.columns if c not in (self.response_col, self.date_col)]
        for f in self.regressors:
            self.model.add_regressor(f)
        data_x[self.regressors] = data_x[self.regressors].astype(float)
        data_x[self.response_col] = data_x[self.response_col].astype(float)
        self.model.fit(data_x.reset_index().rename(columns={self.date_col: 'ds', self.response_col: 'y'}))

    def predict(self, test_x):
        test_x[self.regressors] = test_x[self.regressors].astype(float)
        pred = self.model.predict(test_x.reset_index().rename(columns={self.date_col: 'ds', self.response_col: 'y'}))
        return pred.yhat


# Orbit (DLT)
class CP_Orbit:
    def __init__(self, response_col='prediction', date_col='Date', estimator='stan-map',
                 seasonality=52, seed=2023, global_trend_option='flat', n_bootstrap_draws=400):
        from sklearn.preprocessing import MaxAbsScaler
        self.response_col = response_col
        self.date_col = date_col
        self.estimator = estimator
        self.seasonality = seasonality
        self.seed = seed
        self.global_trend_option = global_trend_option
        self.n_bootstrap_draws = n_bootstrap_draws
        self.sc_in = MaxAbsScaler()
        self.sc_out = MaxAbsScaler()

    def fit(self, data_x):
        from orbit.models import DLT
        regs = [c for c in data_x.columns if c not in (self.response_col, self.date_col)]
        data_x[regs] = data_x[regs].astype(float)
        data_x[self.response_col] = data_x[self.response_col].astype(float)
        data_x.loc[:, regs] = self.sc_in.fit_transform(data_x.loc[:, regs])
        data_x.loc[:, self.response_col] = self.sc_out.fit_transform(data_x[self.response_col].values.reshape(-1, 1))
        self.model = DLT(
            response_col=self.response_col, date_col=self.date_col, regressor_col=regs,
            estimator=self.estimator, seasonality=self.seasonality, seed=self.seed,
            global_trend_option=self.global_trend_option, n_bootstrap_draws=self.n_bootstrap_draws,
        )
        self.model.fit(data_x, point_method="mean")
        self._regs = regs

    def predict(self, test_x):
        test_x[self._regs] = test_x[self._regs].astype(float)
        test_x.loc[:, self._regs] = self.sc_in.transform(test_x.loc[:, self._regs])
        pred = self.model.predict(df=test_x)
        pred.loc[:, 'prediction'] = self.sc_out.inverse_transform(pred['prediction'].values.reshape(-1, 1))
        return np.array(pred.prediction)


# --- 2d. Evaluator + metrics ---

import sklearn.metrics as skm

def cp_preprocess_for_classification(pred, target, is_regression=False):
    if is_regression:
        y, p = [], []
        for i in range(len(target) - 1):
            y.append(target[i + 1] - target[i] > 0)
            p.append(pred[i + 1] - pred[i] > 0)
        return y, p
    return target, pred

def cp_accuracy(pred, target, is_regression=False):
    y, p = cp_preprocess_for_classification(pred, target, is_regression)
    return skm.accuracy_score(y, p)

def cp_f1(pred, target, is_regression=False):
    y, p = cp_preprocess_for_classification(pred, target, is_regression)
    return skm.f1_score(y, p)

def cp_precision(pred, target, is_regression=False):
    y, p = cp_preprocess_for_classification(pred, target, is_regression)
    return skm.precision_score(y, p)

def cp_recall(pred, target, is_regression=False):
    y, p = cp_preprocess_for_classification(pred, target, is_regression)
    return skm.recall_score(y, p)

def cp_rmse(pred, target, **_):
    return math.sqrt(np.square(np.subtract(pred, target)).mean())

def cp_mae(pred, target, **_):
    return np.absolute(pred - target).mean()

def cp_mape(pred, target, **_):
    return np.mean(np.abs((pred - target) / target)) * 100

def cp_smape(pred, target, **_):
    return np.mean(2 * np.abs(pred - target) / (np.abs(target) + np.abs(pred))) * 100

def cp_mase(pred, target, sp=365, **_):
    naive = target[:-sp]
    mae_naive = np.mean(np.abs(target[sp:] - naive))
    return np.nan if mae_naive == 0 else np.mean(np.abs(target - pred)) / mae_naive

def cp_msle(pred, target, **_):
    return np.mean(np.power(np.log(np.array(pred, dtype=float) + 1) - np.log(np.array(target, dtype=float) + 1), 2))


# --- 2e. Backtest strategies ---

def cp_signal1(df):
    """Buy when predicted_mean > prior Close; sell on reversal."""
    pos = False
    sig = [0] * len(df)
    for i in range(1, len(sig)):
        if df['predicted_mean'].iloc[i] > df['Close'].iloc[i - 1]:
            if not pos:
                sig[i] = 2  # buy
                pos = True
        else:
            if pos:
                sig[i] = 1  # sell
                pos = False
    return sig


def cp_signal2(df):
    """Buy when predicted_high exceeds all 10 prior Highs; sell when predicted_low below all 10 prior Lows."""
    sig = [0] * len(df)
    for i in range(10, len(sig)):
        buy = all(df['predicted_high'].iloc[i] >= df['High'].iloc[i - j] for j in range(10))
        if buy:
            sig[i] = 2
        sell = all(df['predicted_low'].iloc[i] <= df['Low'].iloc[i - j] for j in range(10))
        if sell:
            sig[i] = 1
    return sig


# --- 2f. Profit calculator (factory/profit_calculator.py) ---

def cp_profit_pipeline(model_cls, model_kwargs, dataset_with_hl, dataset_main,
                        profit_calc_df, mean_prediction, train_start, train_end, valid_start, valid_end):
    """
    Three-headed prediction: retrain for low, retrain for high, combine with mean.
    Returns DataFrame with predicted_low, predicted_high, predicted_mean, signal1, signal2.
    """
    def split(df):
        tr = df[(df['Date'] > train_start) & (df['Date'] < train_end)]
        va = df[(df['Date'] > valid_start) & (df['Date'] < valid_end)]
        return tr, va

    # Low
    tmp = dataset_with_hl.drop(['predicted_high'], axis=1).rename(columns={'predicted_low': 'prediction'})
    tr, va = split(tmp)
    m_low = model_cls(**model_kwargs)
    m_low.fit(tr)
    pred_low = m_low.predict(va.drop(['prediction'], axis=1))

    # High
    tmp = dataset_with_hl.drop(['predicted_low'], axis=1).rename(columns={'predicted_high': 'prediction'})
    tr, va = split(tmp)
    m_high = model_cls(**model_kwargs)
    m_high.fit(tr)
    pred_high = m_high.predict(va.drop(['prediction'], axis=1))

    # Combine
    _, valid_profit = split(profit_calc_df)
    valid_profit = valid_profit.reset_index(drop=True)
    arr = np.row_stack((pred_low, pred_high, mean_prediction)).T
    preds = pd.DataFrame(arr, columns=['predicted_low', 'predicted_high', 'predicted_mean'])
    combined = pd.concat([valid_profit, preds], axis=1)
    combined['signal1'] = cp_signal1(combined)
    combined['signal2'] = cp_signal2(combined)
    return combined
