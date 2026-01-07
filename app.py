import feedparser
import validators
from flask import Flask, render_template, request
from pathlib import Path
import threading
from datetime import datetime, timezone
from time import mktime
import argparse


# Definizione degli argomenti a linea di comando
argparser = argparse.ArgumentParser('RSS aggregator')
argparser.add_argument('--feed-sources', type=str, required=False, default='feed_sources.txt', help='Input text file with all RSS sources.')
argparser.add_argument('--article-save-path', type=str, required=False, help='Output text file with basic info of all RSS articles.')
args = argparser.parse_args()
FEED_SOURCE_FILE_NAME = args.feed_sources

if 'article_save_path' in args:
    ARTICLE_SAVE_PATH = args.article_save_path
else:
    ARTICLE_SAVE_PATH = None

MAX_FEED_FILE_LINE_NUMBER = 100 # modificare questa costante se è necessario aggregare molte sorgenti

ARTICLES_PER_PAGE = 10 # valore utilizzato nella paginazione dei risultati

FEED_REFRESH_DELAY = 60 # quantità di tempo in secondi che intercorrerà fra due refresh dei feed

MAX_FEED_FILE_LINE_LENGTH = 1000
MAX_SEARCH_QUERY_LENGTH = 1000

MAX_THREAD_WAIT_SECONDS = 10 # massimo numero di secondi che ogni sorgente ha per ritornare il proprio feed

# i feed verranno indicizzati in un dizionario {source: url}
RSS_FEEDS = {}


feed_file_path = Path(FEED_SOURCE_FILE_NAME)
if not feed_file_path.exists() or not feed_file_path.is_file():
    print(f'\'{feed_file_path}\' is not a valid file name or does not exists.')

# ottenimento dei vari feed da file
with open(feed_file_path, 'r') as file:

    # ogni riga del file viene letta sequenzialmente finche' non si raggiunge EOF
    # si tiene traccia del numero di feed letti fino ad ora per non oltrepassare il massimo cosentito
    for line_number, line in enumerate(file):

        # controllo sul raggiungimento del massimo numero di feed
        if line_number >= MAX_FEED_FILE_LINE_NUMBER:
            print(f'Maximum feed file line number reached ({MAX_FEED_FILE_LINE_NUMBER}). Ignoring other lines.')
            break

        if len(line) >= MAX_FEED_FILE_LINE_LENGTH:
            print(f'Line {line_number} has reached the maximum line length. Ignoring it.')
            continue

        line = line.strip()

        # controllo se la riga è un commento o una riga vuota
        if line.startswith('#') or len(line) == 0:
            continue

        # Ogni riga deve contenere il nome del feed ed il suo URL, separati dalla sequenza '>>>'
        tokens = line.split('>>>')

        if len(tokens) != 2:
            print(f'Line {line_number} is not of the correct format (it has not a single \'>>>\' sequence). Ignoring it.')
            continue

        feed_source = tokens[0].strip()
        feed_url = tokens[1].strip()

        if not validators.url(feed_url):
            print(f'Line {line_number} contains a string that is not a valid URL. Ignoring it.')
            continue

        RSS_FEEDS[feed_source] = feed_url


# elenco degli articoli da presentare, in una lista di tuple
# (sorgente, titolo, link all'articolo, timestamp normalizzato)
articles = []

# la precedente lista sarà letta e modificata da diversi thread, quindi avrà bisogno di un lock
article_thread_lock = threading.Lock()


class RssParser(threading.Thread):
    
    def __init__(self, source, url, article_list, thread_lock):
        threading.Thread.__init__(self)
        self.source = source
        self.url = url
        self.thread_lock = thread_lock
        self.article_list = article_list

    def run(self):
        
        # parsing del feed in parallelo
        parsed_feed = feedparser.parse(self.url)

        if parsed_feed.status != 200:
            print(f'The request for \'{self.source}\' with url \'{self.url}\' has returned a status of {parsed_feed.status}')
            return

        # ottenimento degli articoli in una variabile locale, per non stressare l'accesso alla risorsa comune 'articles'
        thread_local_data = threading.local()
        thread_local_data.thread_articles = []

        for entry in parsed_feed.entries:
            
            if 'title' in entry:
                title = entry.title
            else:
                title = None
            
            if 'link' in entry:
                link_url = entry.link
            else:
                link_url = None
            
            if 'published_parsed' in entry:
                timestamp = entry.published_parsed
            else:
                timestamp = None
            
            # ogni articolo sarà memorizzato come tupla (sorgente, titolo, link all'articolo, timestamp normalizzato)
            thread_local_data.thread_articles.append((self.source, title, link_url, datetime.fromtimestamp(mktime(timestamp), timezone.utc)))
        
        # aggiunta degli articoli alla lista comune a tutti i thread
        if not self.thread_lock.acquire(blocking=True, timeout=10):
            print(f'Feed with source \'{self.source}\' could not be appended in time.')
        else:
            self.article_list.extend(thread_local_data.thread_articles)
            self.thread_lock.release()


# funzione che effettua il refresh della lista degli articoli (thread-safe)
def refresh_articles():

    global articles
    global article_thread_lock

    # per non stressare l'accesso alla reale lista di articoli, si ricorre ad una lista temporanea locale
    temp_article_list = []

    # dato che la lista temporanea di articoli sarà popolata in multithreading, avrà bisogno di un lock locale da condividere con ogni thread
    temp_thread_lock = threading.Lock()
    
    # Creazione della lista di thread con ognuno il proprio feed RSS
    threads = []
    for source, url in RSS_FEEDS.items():

        thread = RssParser(source, url, temp_article_list, temp_thread_lock)
        threads.append(thread)

        # de-commentare queste righe se non si vuole il multithreading nel parsing RSS
        # (in tal caso bisogna eliminare anche le righe successive, dato che non vi sono più thread da joinare)
        # parsed_feed = feedparser.parse(url)
        # entries = [(source, entry) for entry in parsed_feed.entries]
        # articles.extend(entries)

    for thread in threads:
        thread.daemon = True
        thread.start()
    for thread in threads:
        thread.join(MAX_THREAD_WAIT_SECONDS) # se il thread non termina entro MAX_THREAD_WAIT_SECONDS viene ucciso

    # ordinamento degli articoli a partire dal più recente
    temp_article_list.sort(key=lambda x: x[3], reverse=True)

    # modifica della reale lista di articoli
    if not article_thread_lock.acquire(blocking=True, timeout=10):
        print(f'Articles could not be accessed for refreshing in time.')
    else:
        articles = temp_article_list
        article_thread_lock.release()


# funzione che salva su file le informazioni degli articoli
def save_articles(save_file_path):

    global articles
    global article_thread_lock

    if save_file_path is None:
        return

    if not article_thread_lock.acquire(blocking=True, timeout=10):
        print(f'Articles could not be accessed for saving in time.')
    else:

        if len(articles) == 0:
            print('There are no articles to save')
            return
        
        with open(save_file_path, 'w') as file:
            for source, title, link_url, timestamp in articles:
                file.write(f'{source}\t{timestamp}\t{title}\t{link_url}\n')
        
        article_thread_lock.release()


# classe che gestisce l'aggiornamento ed il salvataggio su file periodico dei feed
class RefreshArticlesTimer(threading.Timer):
    def run(self):
        while not self.finished.wait(self.interval):
            refresh_articles()
            save_articles(ARTICLE_SAVE_PATH)


# creazione di un'app Flask
app = Flask(__name__)


# homepage con tutti gli articoli (paginati)
@app.route('/')
def index():

    global articles
    global article_thread_lock

    if not article_thread_lock.acquire(blocking=True, timeout=10):
        print(f'Articles could not be accessed in time.')
    else:

        # paginazione dei risultati
        page = request.args.get('page', 1, type=int)
        total_article_number = len(articles)

        if page <= 0:
            page = 1
        max_page_number = total_article_number // ARTICLES_PER_PAGE
        if page > max_page_number:
            page = max_page_number
        if total_article_number == 0:
            page = 1

        start = max(0, (page - 1) * ARTICLES_PER_PAGE)
        end = min(total_article_number, start + ARTICLES_PER_PAGE)
        paginated_articles = articles[start:end]

        article_thread_lock.release()

    return render_template('index.html',
        articles=paginated_articles,
        page=page, total_pages = total_article_number // ARTICLES_PER_PAGE + 1)


# pagina di risposta successiva alla ricerca di una parola chiave
@app.route('/search')
def search():

    global articles
    global article_thread_lock

    # query contiene la stringa da ricercare nei titoli degli articoli
    query = request.args.get('q')

    query = query.strip()
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        print(f'The query is too long!')

    if not article_thread_lock.acquire(blocking=True, timeout=10):
        print(f'Articles could not be accessed in time.')
    else:

        # selezione degli articoli attinenti alla query
        results = [article for article in articles if query.lower() in article[1].lower()]

        article_thread_lock.release()

    return render_template('search_results.html', articles=results, query=query)


if __name__ == '__main__':

    # popolamento iniziale della lista di articoli
    refresh_articles()
    save_articles(ARTICLE_SAVE_PATH)

    # inizio dell'aggiornamento periodico dei feed
    refresh_articles_timer = RefreshArticlesTimer(interval=FEED_REFRESH_DELAY, function=None)
    refresh_articles_timer.start()

    # ATTENZIONE: il server è http e non https, per renderlo sicuro serve configurare i certificati...
    app.run()
    # app.run(debug=True)

    refresh_articles_timer.cancel()
    print('Exiting application...')