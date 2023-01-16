import os, json, math
from celery import Celery
from celery.utils.log import get_task_logger
from time import sleep
from pybit import inverse_perpetual, usdt_perpetual
# from message import sendMessage
import datetime as dt
from datetime import datetime
import redis
import discord
import time
from discord.ext import tasks, commands
from pythonping import ping
from math import trunc


session = inverse_perpetual.HTTP(
    endpoint='https://api.bybit.com'
)

LOCAL = False

try:
    import config
    LOCAL = True
    REDIS_URL = config.REDIS_URL
    DISCORD_CHANNEL = config.DISCORD_CHANNEL
    DISCORD_TOKEN = config.DISCORD_TOKEN
    DISCORD_USER = config.DISCORD_USER
    r = redis.from_url(REDIS_URL, ssl_cert_reqs=None, decode_responses=True)
except:
    REDIS_URL = os.getenv('CELERY_BROKER_URL')
    DISCORD_CHANNEL = os.getenv('DISCORD_CHANNEL')
    DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
    DISCORD_USER = os.getenv('DISCORD_USER')
    r = redis.from_url(REDIS_URL, decode_responses=True)

print('URL', REDIS_URL)
print('REDIS', r)

app = Celery('tasks', broker=REDIS_URL, backend=REDIS_URL)
logger = get_task_logger(__name__)

def getHiLow(timeblocks, coin):

    tbRev = timeblocks[::-1] ## creates a new list  .reverse() change the original list

    ## last block is not completed but does have current HLOC

    LH2h = tbRev[0]['high']
    LL2h = tbRev[0]['low']
    LH2h_index = 0
    LL2h_index = 0
    LH2h_cvd = tbRev[0]['delta_cumulative']
    LL2h_cvd = tbRev[0]['delta_cumulative']


    '''Set locals for the last 2 Hours'''
    count = 0

    for block in tbRev:
        if count <= 23: ### looks at past two hours
            if block['high'] > LH2h:
                LH2h = block['high']
                LH2h_index = count
                LH2h_cvd = block['delta_cumulative']
            if block['low'] < LL2h:
                LL2h = block['low']
                LL2h_index = count
                LL2h_cvd = block['delta_cumulative']
        count += 1

    ''' check if previous candle has an exceeeding cvd'''
    try:
        if tbRev[LH2h_index + 1]['delta_cumulative'] > LH2h_cvd:
            LH2h_cvd = tbRev[LH2h_index + 1]['delta_cumulative']

        if tbRev[LL2h_index + 1]['delta_cumulative'] < LL2h_cvd:
            LL2h_cvd = tbRev[LL2h_index + 1]['delta_cumulative']
    except:
        print('LOCAL CVD CHECK FAIL')

    '''Look for areas where the CVD has already exceeded '''
    recount = 0

    for block in tbRev:
        if recount <= 23 and recount > 1: # discount the first two blocks
            if block['delta_cumulative'] > LH2h_cvd:
                LH2h_cvd = block['delta_cumulative']
            if block['delta_cumulative'] < LL2h_cvd:
                LL2h_cvd = block['delta_cumulative']

        recount += 1

    oih = 0
    oil = 0

    try:
        oih = tbRev[0]['oi_cumulative'] - tbRev[LH2h_index]['oi_cumulative']
        oih = tbRev[0]['oi_cumulative'] - tbRev[LL2h_index]['oi_cumulative']
    except:
        print('OI count FAIL')


    highInfo = {
        'price' : LH2h,
        'index' : LH2h_index,
        'delta' : LH2h_cvd,
        'oi' : oih,
        'div' : False
    }

    lowInfo = {
        'price' : LL2h,
        'index' : LL2h_index,
        'delta' : LL2h_cvd,
        'oi' : oil,
        'div' : False
    }

    if LH2h_index >= 2:
        # current timeblock nor the previous is not the highest/lowest
        if tbRev[0]['delta_cumulative'] > LH2h_cvd:
            # Divergence Triggered
            highInfo['div'] = True
            r.set('discord_' + coin, coin + ' CVD BEAR div: ' + json.dumps(highInfo))
            streamAlert('CVD Bear div: ' + json.dumps(highInfo), 'CVD Divergence', coin)

    if LL2h_index >= 2:
        if tbRev[0]['delta_cumulative'] < LL2h_cvd:
            # Divergence Triggered
            lowInfo['div'] = True
            r.set('discord_' + coin, coin + ' CVD BULL div: ' + json.dumps(lowInfo))
            streamAlert('CVD Bull div: ' + json.dumps(lowInfo), 'CVD Divergence', coin)


    return {'highInfo' : highInfo , 'lowInfo' : lowInfo}


def getHistory(coin):
    # print('GET HISTORY ' + coin)


    if r.get('history_' + coin) == None:
        r.set('history_' + coin, json.dumps([]))

    historyBlocks = json.loads(r.get('history_' + coin))
    if len(historyBlocks) > 0:
        return historyBlocks[-1]
    else:
        return False


def addBlockBlock(blocks, newCandle, timeNow, size, coin):

    print('BLOCK BLOCK 1')
    previousDeltaCum = 0
    previousOICum = 0
    previousTime = 0

    if len(blocks) > 1:
        lastCandle = blocks[-2]
        previousDeltaCum = lastCandle['delta_cumulative']
        previousOICum = lastCandle['oi_cumulative']
        previousTime = lastCandle['trade_time_ms']
    elif getHistory(coin):
        lastCandle = getHistory(coin)['volumeblocks'][-1]
        previousDeltaCum = lastCandle['delta_cumulative']
        previousOICum = lastCandle['oi_cumulative']
        previousTime = lastCandle['trade_time_ms']

    print('BLOCK BLOCK 2')
    currentCandle = blocks[-1]

    if newCandle['low'] < currentCandle['low']:
        currentCandle['low'] = newCandle['low']
    if newCandle['high'] > currentCandle['high']:
        currentCandle['high'] = newCandle['high']

    print('BLOCK BLOCK 3')
    currentCandle['buys'] += newCandle['buys']
    currentCandle['sells'] += newCandle['sells']
    currentCandle['delta'] = currentCandle['buys'] - currentCandle['sells']
    currentCandle['total'] = currentCandle['buys'] + currentCandle['sells']

    currentCandle['close'] = newCandle['close']
    currentCandle['price_delta'] = currentCandle['close'] - currentCandle['open']

    print('BLOCK BLOCK 4')

    currentCandle['delta_cumulative'] =  previousDeltaCum + currentCandle['delta']
    currentCandle['oi_cumulative'] = currentCandle['oi_cumulative'] + newCandle['oi_delta']
    currentCandle['oi_delta'] = currentCandle['oi_cumulative'] - previousOICum
    currentCandle['time_delta'] = timeNow - previousTime


    print('BLOCK BLOCK 5')

    deltaPercent = round( (  currentCandle['delta']  /  (size*1_000_000)  ) * 100  )


    if abs(deltaPercent) > 20:
        if currentCandle['delta'] < 0 and currentCandle['price_delta'] > 4:
            if currentCandle['total'] >= 2_000_000 and size == 2:
                currentCandle['volDivBull2M'] = True
                r.set('discord_' + coin, '2M possible BULL div candle: Delta ' + str(deltaPercent) + '% ' + str(currentCandle['price_delta']) + '$')
            if currentCandle['total'] >= 4_000_000:
                deltaPercent = round((currentCandle['delta']/5_000_000)*100)
                currentCandle['volDivBull5M'] = True
                r.set('discord_' + coin, '5M possible BULL div candle: Delta ' + str(deltaPercent) + '% ' + str(currentCandle['price_delta']) + '$')

        print('BLOCK BLOCK BREAK')

        if currentCandle['delta'] > 0 and currentCandle['price_delta'] < -4:
            if currentCandle['total'] == 2_000_000 and size == 2:
                currentCandle['volDivBear2M'] = True
                r.set('discord_' + coin, '2M possible BEAR div candle: Delta ' + str(deltaPercent) + '% ' + str(currentCandle['price_delta']) + '$')
            if currentCandle['total'] >= 4_000_000:
                currentCandle['volDivBear5M'] = True
                r.set('discord_' + coin, '5M possible BEAR div candle: Delta ' + str(deltaPercent) + '% ' + str(currentCandle['price_delta']) + '$')

    print('BLOCK BLOCK RETURN')

    return currentCandle


def streamAlert(message, mode, coin):
    print('Alert Stream')
    stream = json.loads(r.get('stream_' + coin))

    current_time = dt.datetime.utcnow()
    print('Current Time UTC Alert : ' + str(current_time).split('.')[0])

    alertList = stream['alerts']
    alertMessage = [str(current_time), mode, message]

    alertList.insert(0, alertMessage)

    if len(alertList) > 5:
        alertList.pop()

    r.set('stream_' + coin, json.dumps(stream) )


    ''' alerts notes '''
    # sudden OI change - looks at current candle or infact previous candle if time just passed -
    # perhaps calculate the likely reason

def manageStream(streamTime, streamPrice, streamOI, coin):

    timeblocks = json.loads(r.get('timeblocks_' + coin))
    currentBuys = 0
    currentSells = 0
    if len(timeblocks) > 1:
        currentBuys = timeblocks[-1]['buys']
        currentSells = timeblocks[-1]['sells']
        currentBuys += timeblocks[-2]['buys']
        currentSells += timeblocks[-2]['sells']

    # print('Manage Stream')
    stream = json.loads(r.get('stream_' + coin))
    stream['lastTime'] = streamTime
    stream['lastPrice'] = streamPrice
    stream['lastOI'] = streamOI

    if len(stream['1mOI']) < 2:
        print('INITIAL')
        stream['1mOI'] = [streamTime, streamOI]
    elif streamTime - stream['1mOI'][0] >= 90:

        deltaOI =  streamOI - stream['1mOI'][1]
        deltaOIstr = str(round(deltaOI/100_000)/10) + 'm '
        deltaBuyStr = str(round(currentBuys/1_000)) + 'k '
        deltaSellStr = str(round(currentSells/1_000)) + 'k '

        if deltaOI > stream['oiMarker']:
            message = coin + ' Sudden OI INC: ' + deltaOIstr + ' Buys:' + deltaBuyStr + ' Sells: ' + deltaSellStr + ' Price: ' + str(stream['lastPrice'])
            r.set('discord_' + coin, message)
            streamAlert(message, 'OI', coin)

        if deltaOI < - stream['oiMarker']:
            message = coin + ' Sudden OI DEC: ' + deltaOIstr + ' Buys: ' + deltaBuyStr + ' Sells: ' + deltaSellStr  + ' Price: ' + str(stream['lastPrice'])
            r.set('discord_' + coin, message)
            streamAlert(message, 'OI', coin)


        stream['1mOI'] = [streamTime, streamOI]

    else:
        stream['oi delta'] = [round(streamTime - stream['1mOI'][0]), streamOI - stream['1mOI'][1], '(secs/oi)' ]

    # print(stream)
    r.set('stream_' + coin, json.dumps(stream) )

    return True


def getImbalances(tickList):
    print('IMBALANCES')

    ticks = len(tickList)
    # 1 2 3

    for i in range(ticks):  # 0 1 2
        if i + 1 < ticks:
            print(i, ticks)
            BIbuys = tickList[i]['Buy']
            BIsells = tickList[i + 1]['Sell']

            if BIbuys == 0:
                BIbuys == 1

            BIpct = round((BIbuys / BIsells) * 100)
            if BIpct > 1000:
                BIpct = 1000

            tickList[i + 1]['Buy%'] = BIpct

            SIbuys = tickList[i]['Buy']
            SIsells = tickList[i + 1]['Sell']

            if SIbuys == 0:
                SIbuys == 1

            SIpct = round((SIsells / SIbuys) * 100)
            if SIpct > 1000:
                SIpct = 1000

            tickList[i + 1]['Sell%'] = SIpct

    return tickList



def addBlock(units, blocks, mode, coin):

    CVDdivergence = {}

    if mode == 'timeblock':
        CVDdivergence = getHiLow(blocks, coin)
        stream = json.loads(r.get('stream_' + coin))
        stream['Divs'] = CVDdivergence
        r.set('stream_' + coin, json.dumps(stream) )

    print('ADD BLOCK')

    switch = False

    if mode == 'deltablock':

        try:

            fastCandles = 0

            switchUp = False
            switchDown = False

            if len(blocks) > 2:
                if blocks[-1]['delta'] > 0 and blocks[-2]['delta'] < 0:
                    switchUp = True
                if blocks[-1]['delta'] < 0 and blocks[-2]['delta'] > 0:
                    switchDown = True

            lastElements = [-2, -3, -4, -5, -6]
            timeElements = []

            if len(blocks) >= 7:
                for t in lastElements:
                    timeDelta = blocks[t]['time_delta']/1000
                    timeElements.append(round(timeDelta))
                    if timeDelta < 30:
                        fastCandles += 1


            if fastCandles >= 3:
                if switchUp:
                    switch = True
                    r.set('discord_' + coin, 'Delta Switch Up: ' + json.dumps(timeElements) )
                    streamAlert('Delta Switch Up: ' + json.dumps(timeElements), 'Delta', coin)
                if switchDown:
                    switch = True
                    r.set('discord_' + coin, 'Delta Switch Down: ' + json.dumps(timeElements) )
                    streamAlert('Delta Switch Down: ' + json.dumps(timeElements), 'Delta', coin)



        except:

            r.set('discord_' + coin, 'delta switch fail')

    ''' BLOCK DATA '''

    print('BLOCK DATA: ' + mode + ' -- ' + coin)
    previousOICum = units[0]['streamOI']
    previousTime = units[0]['trade_time_ms']
    newOpen = units[0]['streamPrice']
    price = units[-1]['streamPrice']
    previousDeltaCum = 0

    ## if just one block than that is the current candle
    ## last block is the previous one
    ## but if its the start of the day then we need to get Historical last block

    if len(blocks) > 1:
        if mode == 'carry':
            lastCandle = blocks[-1] # when carrying there is no current candle
        else:
            lastCandle = blocks[-2] # ignore last unit which is the current one
        previousDeltaCum = lastCandle['delta_cumulative']
        previousOICum = lastCandle['oi_cumulative']
        previousTime = lastCandle['trade_time_ms']
        newOpen = lastCandle['close']
    elif 'time' in mode and getHistory(coin):
        lastCandle = getHistory(coin)['timeblocks'][-1]
        previousDeltaCum = lastCandle['delta_cumulative']
        previousOICum = lastCandle['oi_cumulative']



    newStart  = units[0]['trade_time_ms']
    newClose = units[-1]['trade_time_ms']

    # if LOCAL:
    #     print('TIME CHECK', previousTime, newClose, newStart, type(newClose), type(newStart))

    timeDelta = newClose - newStart
    timeDelta2 = newClose - previousTime

    buyCount = 0
    sellCount = 0
    highPrice = 0
    lowPrice = 0

    OIclose = 0
    OIhigh = 0
    OIlow = 0

    tradecount = 0

    tickDict = {}

    oiList = []

    priceList = []

    for d in units:
        # print('BLOCK LOOP', d)

        if d['side'] == 'Buy':
            buyCount += d['size']
        else:
            sellCount += d['size']

        for price in d['spread']:
            price = float(price)
            priceList.append(price)
            # print('SPREAD CHECK', price, type(price) )
            if coin == 'BTC':
                tickPrice = str(trunc(price/10)*10)

            elif coin == 'ETH':
                ##  1159.56 --> 1159.25
                floor = math.floor(price)
                rnd = round(price)
                if floor == rnd:
                    tickPrice = str(floor)
                else:
                    tickPrice = str(floor + 0.5)


            # print('tickPrice', tickPrice)

            if coin == 'BTC':
                # print('TICKES', tickDict, tickPrice)

                if tickPrice not in tickDict:

                    tickDict[tickPrice] = {
                        'tickPrice' : tickPrice,
                        'Sell'  : 0,
                        'Buy' : 0,
                        'Sell%' : 0,
                        'Buy%' : 0
                    }

                tickDict[tickPrice][d['side']] += d['spread'][str(price)] ## the spread keys come back as strings


            oiList.append(d['streamOI'])
            OIclose = d['streamOI']

    highPrice = max(priceList)
    lowPrice = min(priceList)



    tickList = []

    if coin == 'BTC':
        # print('TICKS SORT')

        tickKeys = list(tickDict.keys())
        tickKeys.sort(reverse = True)

        # print('SORT DATA ' + str(priceList))

        for p in tickKeys:
            tickList.append(tickDict[p])

        if 'time' in mode and coin == 'BTC':
            tickList = getImbalances(tickList)

    oiList.sort()

    OIlow = oiList[0]
    OIhigh = oiList[-1]

    delta = buyCount - sellCount
    OIdelta =  OIclose - previousOICum

    # print(coin + ' NC DICT')

    newCandle = {
        'trade_time_ms' : newClose,
        'timestamp' : str(units[0]['timestamp']),
        'time_delta' : timeDelta,
        'close' : price,
        'open' : newOpen,
        'price_delta' : price - newOpen,
        'high' : highPrice,
        'low' : lowPrice,
        'buys' : buyCount,
        'sells' : sellCount,
        'delta' : delta,
        'delta_cumulative' : int(previousDeltaCum + delta),
        'total' : buyCount + sellCount,
        'oi_delta': OIdelta,
        'oi_high': OIhigh,
        'oi_low': OIlow,
        'oi_open': previousOICum,
        'oi_range': OIhigh - OIlow,
        'oi_cumulative': OIclose,
        'divergence' : CVDdivergence,
        'switch' : switch,
        'volcandle_two' : {},
        'volcandle_five' : {},
        'tickList' : tickList,
        'pva_status': {},
        'tradecount': tradecount,
    }

    if 'block' in mode:
        print('NEW CANDLE: ' + mode + ' ' + coin)

    if mode == 'volblock' or mode == 'carry':

        try:
            blockSize = 1_000_000
            if LOCAL:
                blockSize = 100_000

            newCandle['total'] = blockSize


            blocks2m = json.loads(r.get('volumeblocks2m_' + coin))
            if len(blocks2m) == 0:
                blocks2m.append(newCandle)
            elif blocks2m[-1]['total'] < blockSize * 2:
                currentCandle = addBlockBlock(blocks2m, newCandle, newClose, 2, coin)
                blocks2m[-1] = currentCandle
            elif blocks2m[-1]['total'] == blockSize * 2:
                blocks2m.append(newCandle)

            r.set('volumeblocks2m_' + coin, json.dumps(blocks2m))

            blocks5m = json.loads(r.get('volumeblocks5m_' + coin))
            if len(blocks5m) == 0:
                blocks5m.append(newCandle)
            elif blocks5m[-1]['total'] < blockSize * 5:
                newCandle['volcandle_five'] = addBlockBlock(blocks5m, newCandle, newClose, 5, coin)
            elif blocks5m[-1]['total'] == blockSize * 5:
                blocks5m.append(newCandle)

            r.set('volumeblocks5m_' + coin, json.dumps(blocks5m))

        except:
            print('VOLBLOCKS ERROR')


    return newCandle


def getPVAstatus(timeblocks, coin):
    if LOCAL:
        print('GET PVA')
    last11blocks = []
    if len(timeblocks) < 11:
        history = json.loads(r.get('history_' + coin))
        try:
            if len(history) > 0:
                lastHistory = history[-1]['timeblocks_' + coin]
                howManyOldTimeblocks = (11-len(timeblocks))
                last11blocks = lastHistory[-howManyOldTimeblocks:] + timeblocks
                # print('LASTBLOCKS HISTORY', last11blocks)
                ## if one time block - get last 10 from history
                ## if 4 time blocks - get last 7 from history
            else:
                return {}
        except:
            # r.set('discord_' + coin, 'History PVA error')
            print('PVA HISTORY ERROR')
            return {}
    else:
        if len(timeblocks) >= 11:
            try:
                last11blocks = timeblocks[-11:]
            except:
                return {}

        else:
            return {}

    # print('PVA Calculate')

    sumVolume = 0
    lastVolume = 0
    lastDelta = 0
    lastPriceDelta = 0
    lastOIDelta = 0
    lastOIRange = 0

    try:
        count = 1
        for x in last11blocks:
            if count < 11:
                sumVolume += x['total']
                count += 1
            else:
                lastVolume = x['total']
                lastDelta = x['delta']
                lastPriceDelta = x['price_delta']
                lastOIDelta = x['oi_delta']
                lastOIRange = round((x['oi_high'] - x['oi_low'])/100_000)/10

        pva150 = False
        pva200 = False
        divergenceBull = False
        divergenceBear = False
        flatOI = False

        percentage = round((lastVolume/(sumVolume/10)), 2)
        deltapercentage = round((lastDelta/lastVolume)*100, 2)

        if percentage > 2:
            pva200 = True
            if lastOIDelta < 100000  and lastOIDelta > - 100000:
                flatOI = True
        elif percentage > 1.5:
            pva150 = True

        if lastDelta > 0 and lastPriceDelta < 0:
            divergenceBear = True
        elif lastDelta < 0 and lastPriceDelta > 0:
            divergenceBull = True

        returnPVA = {
            'pva150' : pva150,
            'pva200' : pva200,
            'vol': lastVolume,
            'percentage' : percentage,
            'deltapercentage' : deltapercentage,
            'PVAbearDIV' : divergenceBear,
            'PVAbullDIV' : divergenceBull,
            'rangeOI' : lastOIRange,
            'flatOI' : flatOI
            }

        print('RETURN PVA')

        if pva200 and flatOI and lastVolume > 1_000_000:
            r.set('discord_' + coin, coin + ' PVA flatOI  Vol:' + str(returnPVA['vol']) + ' ' + str(returnPVA['percentage']*100) + '%   OI Range: ' + str(returnPVA['rangeOI']) + 'm')
            streamAlert('PVA candle with flat OI', 'PVA', coin)
        elif pva200 and divergenceBear and lastVolume > 1_000_000:
            msg = coin + ' PVA divergence Bear: ' +  str(returnPVA['vol']) + ' ' + str(returnPVA['percentage'])
            r.set('discord_' + coin, msg)
        elif pva200 and divergenceBull and lastVolume > 1_000_000:
            msg = coin + ' PVA divergence Bull: ' +  str(returnPVA['vol']) + ' ' + str(returnPVA['percentage'])
            r.set('discord_' + coin, msg)

        return returnPVA

    except:
        return {}


def logTimeUnit(buyUnit, sellUnit, coin):

    timeflow =  json.loads(r.get('timeflow_' + coin)) # []
    timeblocks = json.loads(r.get('timeblocks_' + coin)) # []

    # print('TIME REDIS', len(timeflow), len(timeblocks))

    if len(timeflow) == 0:
        print('TIME 0 ' + coin)

        ## start the initial time flow and initial current candle
        if buyUnit['size'] > 0:
            timeflow.append(buyUnit)
        if sellUnit['size'] > 0:
            timeflow.append(sellUnit)

        currentCandle = addBlock(timeflow, timeblocks, 'timemode', coin)
        timeblocks.append(currentCandle)

        r.set('timeblocks_' + coin, json.dumps(timeblocks))
        r.set('timeflow_' + coin, json.dumps(timeflow))
    else:
        blockStart = timeflow[0]['trade_time_ms']
        if LOCAL:
            interval = (60000*1) # 1Min
        else:
            interval = (60000*5) # 5Min
        blockFinish = blockStart + interval


        # print('TIME 1')
        if buyUnit['trade_time_ms'] >= blockFinish: # store current candle and start a new Candle
            # print('ADD TIME CANDLE ' + coin)

            # replace current candle with completed candle
            newCandle = addBlock(timeflow, timeblocks, 'timeblock', coin)
            LastIndex = len(timeblocks) - 1
            timeblocks[LastIndex] = newCandle

            timeblocks[LastIndex]['pva_status'] = getPVAstatus(timeblocks, coin)

            # reset timeflow and add new unit
            timeflow = []
            buyUnit['trade_time_ms'] = blockFinish
            sellUnit['trade_time_ms'] = blockFinish
            if buyUnit['size'] > 0:
                timeflow.append(buyUnit)
            if sellUnit['size'] > 0:
                timeflow.append(sellUnit)

            # add fresh current candle to timeblock
            currentCandle = addBlock(timeflow, timeblocks, 'timemode', coin)
            timeblocks.append(currentCandle)
            # print('TIME FLOW RESET: ' + str(len(timeflow)) + ' ' + str(len(timeblocks)))
            r.set('timeblocks_' + coin, json.dumps(timeblocks))
            r.set('timeflow_' + coin, json.dumps(timeflow))

        else: # add the unit to the time flow

            # print('ADD TIME UNIT')
            timeflow.append(buyUnit)
            timeflow.append(sellUnit)

            # update current candle with new unit data
            currentCandle = addBlock(timeflow, timeblocks, 'timemode', coin)
            LastIndex = len(timeblocks) - 1
            timeblocks[LastIndex] = currentCandle
            r.set('timeblocks_' + coin, json.dumps(timeblocks))
            r.set('timeflow_' + coin, json.dumps(timeflow))


def getDeltaStatus(deltaflow, buyUnit, sellUnit):
    print('GET DELTA STATUS')

    deltaBlock = 1_000_000

    if LOCAL:
        deltaBlock = 100_000



    totalBuys = 0
    totalSells = 0
    negDelta = False
    posDelta = False
    excess = 0

    for d in deltaflow:
        if d['side'] == 'Buy':
            totalBuys += d['size']
        if d['side'] == 'Sell':
            totalSells += d['size']


    # if totalBuys - totalSells < - deltaBlock:
    #     negDelta = True
    #     ## there are excess shorts
    #     ##  1M longs  2.5M shorts = delta -1.5m  with 0.5 excess
    #     excess = abs((totalBuys - totalSells) + deltaBlock)


    # if totalBuys - totalSells > deltaBlock:
    #     posDelta = True
    #     ## there are excess long
    #     ##  2.5M longs  1M shorts = delta 1.5m  with 0.5 excess
    #     excess = abs((totalBuys - totalSells) - deltaBlock)

    return {
            'flowdelta' : totalBuys - totalSells,
            'negDelta' : negDelta,
            'posDelta' : posDelta,
            'excess' : excess
    }


def logDeltaUnit(buyUnit, sellUnit, coin):

    # add a new unit which is msg from handle_message

    deltaflow =  json.loads(r.get('deltaflow_' + coin)) # []
    deltablocks = json.loads(r.get('deltablocks_' + coin)) # []

    if LOCAL:
        print('DELTA REDIS', len(deltaflow), len(deltablocks))

    if len(deltaflow) == 0:
        print('DELTA 0')

        ## start the initial time flow and initial current candle
        if buyUnit['size'] > 0:
            deltaflow.append(buyUnit)
        if sellUnit['size'] > 0:
            deltaflow.append(sellUnit)

        currentCandle = addBlock(deltaflow, deltablocks, 'deltamode', coin)
        deltablocks.append(currentCandle)

        r.set('deltablocks_' + coin, json.dumps(deltablocks))
        r.set('deltaflow_' + coin, json.dumps(deltaflow))
    else:

        deltaStatus = getDeltaStatus(deltaflow, buyUnit, sellUnit)

        print('DELTA 1')

        if deltaStatus['posDelta'] or deltaStatus['negDelta']:
            # store current candle and start a new Candle
            print('ADD DELTA CANDLE: ' + json.dumps(deltaStatus))
            if LOCAL:
                r.set('discord_' + coin, 'NEW DELTA: ' +  json.dumps(deltaStatus))

            # replace current candle with completed candle
            newCandle = addBlock(deltaflow, deltablocks, 'deltablock', coin)
            LastIndex = len(deltablocks) - 1
            deltablocks[LastIndex] = newCandle

            # reset deltaflow
            deltaflow = []

            # add fresh current candle to timeblock
            if LOCAL:
                print('DELTA FLOW RESET', len(deltaflow), len(deltablocks))
            r.set('deltablocks_' + coin, json.dumps(deltablocks))
            r.set('deltaflow_' + coin, json.dumps(deltaflow))

        else: # add the unit to the delta flow

            print('ADD DELTA UNIT')

            # update current candle with new unit data
            currentCandle = addBlock(deltaflow, deltablocks, 'deltamode', coin)
            LastIndex = len(deltablocks) - 1
            deltablocks[LastIndex] = currentCandle
            r.set('deltablocks_' + coin, json.dumps(deltablocks))
            r.set('deltaflow_' + coin, json.dumps(deltaflow))



def logVolumeUnit(buyUnit, sellUnit, coin):
    ## load vol flow


    if LOCAL:
        block = 100_000
    else:
        block = 1_000_000

    volumeflow = json.loads(r.get('volumeflow_' + coin)) ## reset after each volume block

    totalMsgSize = buyUnit['size'] + sellUnit['size']

    print(coin + ' LOG VOLUME UNIT ' + str(totalMsgSize))
    ## calculate current candle size
    volumeflowTotal = 0
    for t in volumeflow:
        volumeflowTotal += t['size']

    if volumeflowTotal > block:
        ### Deal with the uncomman event where the last function left an excess on volume flow
        print('VOL FLOW EXCESS ' + str(volumeflowTotal))
        volumeblocks = json.loads(r.get('volumeblocks_' + coin))
        currentCandle = addBlock(volumeflow, volumeblocks, 'volblock', coin)

        LastIndex = len(volumeblocks) - 1
        volumeblocks[LastIndex] = currentCandle

        volumeflow = []

        if buyUnit['size'] > 1:
            volumeflow.append(buyUnit)
        if sellUnit['size'] > 1:
            volumeflow.append(sellUnit)

        currentCandle = addBlock(volumeflow, volumeblocks, 'vol', coin)

        volumeblocks.append(currentCandle)

        r.set('volumeblocks_' + coin, json.dumps(volumeblocks))
        r.set('volumeflow_' + coin, json.dumps(volumeflow))


    elif volumeflowTotal + totalMsgSize <= block:  # Normal addition of trade to volume flow
        # print(volumeflowTotal, '< Block')

        if buyUnit['size'] > 1:
            volumeflow.append(buyUnit)
        if sellUnit['size'] > 1:
            volumeflow.append(sellUnit)


        volumeblocks = json.loads(r.get('volumeblocks_' + coin))
        currentCandle = addBlock(volumeflow, volumeblocks, 'vol', coin)

        LastIndex = len(volumeblocks) - 1
        if LastIndex < 0:
            volumeblocks.append(currentCandle)
        else:
            volumeblocks[LastIndex] = currentCandle

        r.set('volumeblocks_' + coin, json.dumps(volumeblocks))
        r.set('volumeflow_' + coin, json.dumps(volumeflow))

    else: # Need to add a new block

        # print('carryOver')
        # print('new blockkkkk --  Total msg size: ' + str(totalMsgSize) + ' Vol flow total: ' + str(volumeflowTotal))
        lefttoFill = block - volumeflowTotal

        ## split buys and sells evenly
        proportion = lefttoFill/totalMsgSize

        ## left to fill 100_000  totalmsg size 1_300_000  (1_000_000 buys   300_000 sells)
        ## proportion = 0.076

        buyFill = buyUnit['size'] * proportion
        sellFill = sellUnit['size'] * proportion

        buyCopy = buyUnit.copy()
        sellCopy = sellUnit.copy()

        buyCopy['size'] = int(buyFill)
        sellCopy['size'] = int(sellFill)

        if buyCopy['size'] > 0:
            volumeflow.append(buyCopy)
            buyUnit['size'] -= int(buyFill)
        if sellCopy['size'] > 0:
            volumeflow.append(sellCopy)
            sellUnit['size'] -= int(sellFill)

        volumeblocks = json.loads(r.get('volumeblocks_' + coin))
        LastIndex = len(volumeblocks) - 1
        # print('VOL BLOCK BREAK')
        newCandle = addBlock(volumeflow, volumeblocks, 'volblock', coin)
        volumeblocks[LastIndex] = newCandle  # replace last candle (current) with completed

        r.set('volumeblocks_' + coin, json.dumps(volumeblocks))

        ## volume flow has been added as full candle and should be reset
        volumeflow = []

        while buyUnit['size'] > block:
            ## keep appending large blocks
            # r.set('discord_' + coin, 'Carry Over: ' + str(buyUnit['size']) + ' -- ' + str(sellUnit['size']))
            volumeblocks = json.loads(r.get('volumeblocks_' + coin))
            newUnit = buyUnit.copy()
            newUnit['size'] = block
            buyUnit['size'] = buyUnit['size'] - block
            newCandle = addBlock([newUnit], volumeblocks, 'carry', coin)
            volumeblocks.append(newCandle)
            r.set('volumeblocks_' + coin, json.dumps(volumeblocks))

        while sellUnit['size'] > block:
            ## keep appending large blocks
            # r.set('discord_' + coin, 'Carry Over: ' + str(buyUnit['size']) + ' -- ' + str(sellUnit['size']))
            volumeblocks = json.loads(r.get('volumeblocks_' + coin))
            newUnit = sellUnit.copy()
            newUnit['size'] = block
            sellUnit['size'] = sellUnit['size'] - block
            newCandle = addBlock([newUnit], volumeblocks, 'carry', coin)
            volumeblocks.append(newCandle)
            r.set('volumeblocks_' + coin, json.dumps(volumeblocks))

        if buyUnit['size'] + sellUnit['size']  >  block:
            ## This is very unlikley so just set an alert
            r.set('discord_' + coin, 'Unlikley Carry: ' + str(buyUnit['size']) + ' -- ' + str(sellUnit['size']))


        # Create new flow block with left over contracts
        if buyUnit['size'] > 1:
            volumeflow.append(buyUnit)
        if sellUnit['size'] > 1:
            volumeflow.append(sellUnit)

        volumeblocks = json.loads(r.get('volumeblocks_' + coin))
        currentCandle = addBlock(volumeflow, volumeblocks, 'vol', coin)
        volumeblocks.append(currentCandle)
        r.set('volumeblocks_' + coin, json.dumps(volumeblocks))
        r.set('volumeflow_' + coin, json.dumps(volumeflow))


def getPreviousDay(blocks):

    try:
        dailyOpen = blocks[0]['open']
        dailyClose = blocks[-1]['close']
        dailyPriceDelta = dailyClose - dailyOpen
        dailyCVD = blocks[-1]['delta_cumulative']
        dailyDIV = False

        if dailyPriceDelta < 0 and dailyCVD > 0:
            dailyDIV = True
        elif dailyPriceDelta > 0 and dailyCVD < 0:
            dailyDIV = True

        dailyVolume = 0

        for b in blocks:
            dailyVolume += b['total']

        return json.dumps({
            'VOL: ' : round(dailyVolume/100_000)/10,
            'CVD:' : round(dailyCVD/100_000)/10,
            'Price:' : dailyPriceDelta
            })

    except:
        return 'getPreviousDay() fail'


def historyReset(coin):
    current_time = dt.datetime.utcnow()

    dt_string = current_time.strftime("%d/%m/%Y")

    if current_time.hour == 23 and current_time.minute == 59:
        print('History Reset Current Time UTC : ' + str(current_time))
        history = json.loads(r.get('history_' + coin))

        vb = json.loads(r.get('volumeblocks_' + coin))
        tb = json.loads(r.get('timeblocks_' + coin))
        db = json.loads(r.get('deltablocks_' + coin))
        vb2 = json.loads(r.get('volumeblocks2m_' + coin))
        vb5 = json.loads(r.get('volumeblocks5m_' + coin))

        pdDict = {
                    'date' : dt_string,
                    'timeblocks' : tb,
                    'deltablocks' : db,
                    'volumeblocks' : vb,
                    'volumeblocks2m' : vb2,
                    'volumeblocks5m' : vb5,
                }

        if len(history) > 0:
            lastHistory = json.loads(r.get('history_' + coin))[len(history)-1]

            if lastHistory['date'] != dt_string:
                print('REDIS STORE', dt_string)

                history.append(pdDict)

                pd = getPreviousDay(tb)

                r.set('history_' + coin, json.dumps(history))
                r.set('discord_' + coin, coin + ' history log: ' + pd)
        else:
            print('REDIS STORE INITIAL')

            history.append(pdDict)

            pd = getPreviousDay(tb)

            r.set('history_' + coin, json.dumps(history))
            r.set('discord_' + coin, 'history log: ' + pd)

    if current_time.hour == 0 and current_time.minute == 0:
        print('REDIS RESET', current_time)
        if r.get('newDay_' + coin) != dt_string:
            print('REDIS RESET')


            r.set('volumeflow_' + coin, json.dumps([]) )  # this the flow of message data for volume candles
            r.set('volumeblocks_' + coin, json.dumps([]) )  #  this is the store of volume based candles
            r.set('volumeblocks2m_' + coin, json.dumps([]) )  #  this is the store of volume based candles
            r.set('volumeblocks5m_' + coin, json.dumps([]) )  #  this is the store of volume based candles
            r.set('deltaflow_' + coin, json.dumps([]) )  # this the flow of message data to create next candle
            r.set('deltablocks_' + coin, json.dumps([]) ) # this is the store of new time based candles


            r.set('timeflow_' + coin, json.dumps([]) )  # this the flow of message data to create next candle
            r.set('timeblocks_' + coin, json.dumps([]) ) # this is the store of new time based candles
            r.set('newDay_' + coin, dt_string)


            r.set('discord_' + coin, coin + ' new day')

    return True


def compiler(message, pair, coin):

    timestamp = message[0]['timestamp']
    ts = str(datetime.strptime(timestamp.split('.')[0], "%Y-%m-%dT%H:%M:%S"))

    sess = session.latest_information_for_symbol(symbol=pair)

    streamTime = round(float(sess['time_now']), 1)
    streamPrice = float(sess['result'][0]['last_price'])
    streamOI = sess['result'][0]['open_interest']

    manageStream(streamTime, streamPrice, streamOI, coin)

    buyUnit = {
                    'side' : 'Buy',
                    'size' : 0,
                    'trade_time_ms' : int(message[0]['trade_time_ms']),
                    'timestamp' : ts,
                    'streamTime' : streamTime,
                    'streamPrice' : streamPrice,
                    'streamOI' : streamOI,
                    'tradecount' : 0,
                    'spread' : {}
                }

    sellUnit = {
                    'side' : 'Sell',
                    'size' : 0,
                    'trade_time_ms' : int(message[0]['trade_time_ms']),
                    'timestamp' : ts,
                    'streamTime' : streamTime,
                    'streamPrice' : streamPrice,
                    'streamOI' : streamOI,
                    'tradecount' : 0,
                    'spread' : {}
    }


    for x in message:
        priceString = str(round(float(x['price'])*2)/2)

        if x['side'] == 'Buy':
            spread = buyUnit['spread']
            if priceString not in spread:
                spread[priceString] = x['size']
            else:
                spread[priceString] += x['size']

            buyUnit['size'] += x['size']
            buyUnit['tradecount'] += 1

        if x['side'] == 'Sell':
            spread = sellUnit['spread']
            if priceString not in spread:
                spread[priceString] = x['size']
            else:
                spread[priceString] += x['size']

            sellUnit['size'] += x['size']
            sellUnit['tradecount'] += 1



    totalMsgSize = int(buyUnit['size'] + sellUnit['size'])

    if  totalMsgSize > 1_000_000:
        bString = 'Buy: ' + str(buyUnit['size'])  + ' Sell: ' + str(sellUnit['size'])
        print('Large Trade: ' + bString)
        r.set('discord_' + coin,  'Large Trade: ' + bString)

    print(coin + ' COMPILER RECORD:  Buys - ' + str(buyUnit['size']) + ' Sells - ' + str(sellUnit['size']))


    return [buyUnit, sellUnit]


def handle_trade_message(msg):

    pair = msg['topic'].split('.')[1]
    coin = pair.split('USD')[0]


    ### check time and reset
    historyReset(coin)

    print(coin + ' handle_trade_message: ' + str(len(msg['data'])))
    # print(msg['data'])


    compiledMessage = compiler(msg['data'], pair, coin)

    buyUnit = compiledMessage[0]
    sellUnit = compiledMessage[1]

    logTimeUnit(buyUnit, sellUnit, coin)

    if coin == 'BTC':
        logVolumeUnit(buyUnit, sellUnit, coin)

    # logDeltaUnit(buyUnit, sellUnit)



def startDiscord():
    ## intents controls what the bot can do; in this case read message content
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True
    bot = commands.Bot(command_prefix="!", intents=discord.Intents().all())

    @bot.event
    async def on_ready():
        print(f'{bot.user} is now running!')
        user = bot.get_user(int(DISCORD_USER))
        print('DISCORD_GET USER', DISCORD_USER, 'user=', user)
        await user.send('Running')
        checkRedis.start(user)

    @tasks.loop(seconds=3)
    async def checkRedis(user):
        print('DISCORD REDIS CHECK')


        coinList = ['BTC', 'ETH']

        for coin in coinList:

            ## need incase redis gets wiped
            if not r.get('discord_' + coin):
                r.set('discord_' + coin, 'discord set')


            if r.get('discord_' + coin) != 'blank':
                await user.send(r.get('discord_' + coin))
                r.set('discord_' + coin, 'blank')

    @bot.event
    async def on_message(msg):
        user = bot.get_user(int(DISCORD_USER))
        print('MESSAGE DDDDDDDDD', msg.content)
        replyText = 'ho'

        if msg.author == user:
            await user.send(replyText)
            # ping('rekt-app.onrender.com', verbose=True)


    bot.run(DISCORD_TOKEN)


@app.task() #bind=True, base=AbortableTask  // (self)
def runStream():

    print('RUN_STREAM')

    rDict = {
        'lastPrice' : 0,
        'lastTime' : 0,
        'lastOI' : 0,
        '1mOI' : [],
        'oiMarker' : 1000000,
        'Divs' : {},
        'alerts' : []
    }

    coins = ['BTC', 'ETH', 'GALA', 'SOL']

    for c in coins:
        r.set('stream_' + c, json.dumps(rDict) )
        r.set('timeflow_' + c, json.dumps([]) )  # this the flow of message data to create next candle
        r.set('timeblocks_' + c, json.dumps([]) ) # this is the store of new time based candles


        r.set('volumeflow_' + c, json.dumps([]) )  # this the flow of message data for volume candles
        r.set('volumeblocks2m_' + c, json.dumps([]) )  #  this is the store of volume based candles
        r.set('volumeblocks5m_' + c, json.dumps([]) )  #  this is the store of volume based candles
        r.set('volumeblocks_' + c, json.dumps([]) )  #  this is the store of volume based candles

        r.set('deltaflow_' + c, json.dumps([]) )
        r.set('deltablocks_' + c, json.dumps([]) )

        # r.set('history_' + c, json.dumps([]) )


    print('WEB_SOCKETS')

    ws_inverseP = inverse_perpetual.WebSocket(
        test=False,
        ping_interval=30,  # the default is 30
        ping_timeout=None,  # the default is 10 # set to None and it will never timeout?
        domain="bybit"  # the default is "bybit"
    )

    ws_inverseP.trade_stream(
        handle_trade_message, ["BTCUSD", "ETHUSD"]
    )

    ws_usdtP = usdt_perpetual.WebSocket(
        test=False,
        ping_interval=30,  # the default is 30
        ping_timeout=None,  # the default is 10 # set to None and it will never timeout?
        domain="bybit"  # the default is "bybit"
    )

    ws_usdtP.trade_stream(
        handle_trade_message, ["SOLUSDT"]
    )


    # ws_inverseP.instrument_info_stream(
    #     handle_info_message, "BTCUSD"
    # )

    startDiscord()

    while True:
        sleep(0.1)

    return print('Task Closed')


if LOCAL:
    runStream()





