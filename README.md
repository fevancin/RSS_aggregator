# Aggregatore di sorgenti RSS in Python con Flask

## Dipendenze non presenti nella libreria standard
- flask
- feedparser
- validators

Il progetto prevede un singolo file 'app.py' che gestisce la lettura delle sorgenti da file testuale, oltre ai template HTML+CSS per la visualizzazione web.

## Utilizzo
Per avviare l'applicazione eseguire il comando `python app.py` oppure `python app.py --feed-sources <PATH>` se si vuole utilizzare uno specifico file con le sorgenti RSS (default `feed_sources.txt`). Questo file verrà letto una sola volta, e dunque successive modifiche renderanno necessario il restart dello script.

E' previsto l'ulteriore argomento `--article-save-path <PATH>` che, se specificato, crea un file con l'elenco di informazioni raccolte.

## Formato dei file
Il file con le sorgenti dei feed deve presentare, per ogni riga, una coppia 'sorgente >>> url' separata dalla stringa '>>>'.
Eventuali righe vuote o che iniziano col carattere '#' sono ignorate.

L'eventuale file di output conterrà una riga per ogni articolo raccolto, nella forma 'sorgente timestamp titolo link', con i vari token separati da un carattere di tabulazione.