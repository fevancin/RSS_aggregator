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


# elenco degli articoli da presentare, in una lista di coppie (sorgente, articolo).
# successivamente questa stessa lista verrà sovrascritta trasformando gli elementi in tuple
# (sorgente, titolo, link all'articolo, timestamp normalizzato)
articles = []

# dato che la precedente lista sarà creata in multithreading, avrà bisogno di un lock
thread_lock = threading.Lock()

class RssParser(threading.Thread):
    
    def __init__(self, source, url):
        threading.Thread.__init__(self)
        self.source = source
        self.url = url

    def run(self):
        
        # parsing del feed in parallelo
        parsed_feed = feedparser.parse(self.url)

        if parsed_feed.status != 200:
            print(f'The request for \'{source}\' with url \'{url}\' has returned a status of {parsed_feed.status}')
            return

        # ottenimento degli articoli in una variabile locale, per non stressare l'accesso alla risorsa comune 'articles'
        thread_local_data = threading.local()
        thread_local_data.thread_articles = []

        entries = [(self.source, entry) for entry in parsed_feed.entries]
        thread_local_data.thread_articles.extend(entries)
        
        # aggiunta degli articoli alla lista comune a tutti i thread
        if not thread_lock.acquire(blocking=True, timeout=10):
            print(f'Feed with source \'{source}\' could not be appended in time.')
        else:
            articles.extend(thread_local_data.thread_articles)
            thread_lock.release()

# Creazione della lista di thread con ognuno il proprio feed RSS
threads = []
for source, url in RSS_FEEDS.items():

    thread = RssParser(source, url)
    threads.append(thread)

    # de-commentare queste righe se non si vuole il multithreading nel parsing RSS
    # parsed_feed = feedparser.parse(url)
    # entries = [(source, entry) for entry in parsed_feed.entries]
    # articles.extend(entries)

for thread in threads:
    thread.daemon = True
    thread.start()
for thread in threads:
    thread.join(MAX_THREAD_WAIT_SECONDS)

# ogni articolo sarà memorizzato come tupla (sorgente, titolo, link all'articolo, timestamp normalizzato)
for i in range(len(articles)):

    source = articles[i][0]
    article = articles[i][1]
    
    if 'title' in article:
        title = article.title
    else:
        title = None
    
    if 'link' in article:
        link_url = article.link
    else:
        link_url = None
    
    if 'published_parsed' in article:
        timestamp = article.published_parsed
    else:
        timestamp = None
    
    articles[i] = (source, title, link_url, datetime.fromtimestamp(mktime(timestamp), timezone.utc))

# ordinamento degli articoli a partire dal più recente
articles = sorted(articles, key=lambda x: x[3], reverse=True)


# creazione di un'app Flask
app = Flask(__name__)

@app.route('/')
def index():

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

    return render_template('index.html',
        articles=paginated_articles,
        page=page, total_pages = total_article_number // ARTICLES_PER_PAGE + 1)


@app.route('/search')
def search():

    # query contiene la stringa da ricercare nei titoli degli articoli
    query = request.args.get('q')

    query = query.strip()
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        print(f'The query is too long!')

    # selezione degli articoli attinenti alla query
    results = [article for article in articles if query.lower() in article[1].lower()]

    return render_template('search_results.html', articles=results, query=query)


# funzione che salva su file tutte le informazioni raccolte dall'aggregatore
def article_saving_thread(save_file_path, articles):
    with open(save_file_path, 'w') as file:
        for source, title, link_url, timestamp in articles:
            file.write(f'{source}\t{timestamp}\t{title}\t{link_url}\n')


if __name__ == '__main__':

    # salvataggio delle informazioni degli articoli raccolte
    if ARTICLE_SAVE_PATH is not None:
        if len(articles) == 0:
            print('There are no articles to save')
        else:
            file_saving_thread = threading.Thread(target=article_saving_thread, args=(ARTICLE_SAVE_PATH, articles))
            file_saving_thread.daemon = True
            file_saving_thread.start()

    # ATTENZIONE: il server è http e non https, per renderlo sicuro serve configurare i certificati...
    app.run()
    # app.run(debug=True)

    # questa soluzione non mi pare molto elegante... aspetto un thread dopo che il server HTTP è partito.
    # per qualche motivo creare un thread dedicato ad app.run() dell'applicazione Flask non funziona.
    file_saving_thread.join(MAX_THREAD_WAIT_SECONDS)
    print('Exiting application...')