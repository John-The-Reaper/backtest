# Comment Transformer Un Signal Statistique En Strategie De Trading Crypto

## L'idee de depart

Au depart, la question est simple :

**comment savoir si le prix d'une cryptomonnaie est dans une situation "normale" ou s'il s'eloigne trop de son comportement habituel ?**

Dans les marches financiers, les prix montent, baissent, accelerent, puis reviennent parfois vers une zone plus stable.  
L'idee du projet est donc la suivante :

**si on arrive a mesurer qu'un prix s'eloigne anormalement de sa moyenne, on peut peut-etre en faire un signal de trading.**

C'est la que le **z-score** entre en jeu.

## Le role du z-score

Le z-score est un outil statistique tres simple a comprendre.

Il sert a mesurer a quel point une valeur est eloignee de sa moyenne.

Dans notre cas :

- si le z-score est proche de 0, le prix est dans une zone plutot normale
- si le z-score devient tres negatif, le prix est plus bas que d'habitude
- si le z-score devient tres positif, le prix est plus haut que d'habitude

Autrement dit, le z-score nous aide a **reperer des anomalies ou des exces de marche**.

L'hypothese de travail du projet est donc :

**quand un prix s'eloigne trop fortement de sa moyenne, il peut avoir tendance a revenir vers cette moyenne.**

C'est ce qu'on appelle une logique de **retour a la moyenne**.

## Transformer une idee en projet concret

Une idee seule ne suffit pas.  
Il faut maintenant la transformer en systeme testable.

Le projet a donc ete construit en plusieurs etapes simples :

1. recuperer des donnees de marche
2. organiser ces donnees proprement
3. calculer le z-score
4. transformer ce calcul en regles d'entree et de sortie
5. tester automatiquement la strategie sur des donnees passees
6. analyser les resultats

L'objectif n'est pas de predire l'avenir.  
L'objectif est plus modeste et plus rigoureux :

**verifier si une idee statistique produit ou non un comportement interessant dans un cadre de backtest.**

## La recuperation des donnees

Avant de parler de trading, il faut parler de donnees.

Une strategie quantitative depend entierement de la qualite de ses donnees.  
Si les donnees sont mauvaises, incompletes ou mal rangees, les resultats ne veulent plus dire grand-chose.

Dans ce projet, les donnees crypto sont gerees par un composant central : le `DataManager`.

Son role est de :

- telecharger les donnees depuis un exchange quand il le faut
- ou charger directement des fichiers `.feather` deja existants
- filtrer les donnees entre une date de debut et une date de fin
- renvoyer des tableaux propres exploitables par la strategie

Le format `.feather` est utile parce qu'il permet de stocker les donnees rapidement et proprement.  
Cela evite de telecharger les memes informations a chaque execution.

Donc, dans la pratique, le projet peut soit :

- recuperer des donnees fraiches
- soit reutiliser des donnees locales deja preparees

Cette etape est essentielle, parce qu'elle rend le projet plus rapide, plus stable, et plus pratique a tester.

## La construction du signal

Une fois les donnees recuperees, la strategie va regarder les prix de cloture.

Elle calcule ensuite :

- une moyenne mobile sur une fenetre donnee
- un ecart-type, c'est-a-dire une mesure de dispersion
- puis le z-score

Cela permet de repondre a la question :

**est-ce que le prix actuel est tres loin de son comportement recent ?**

Si la reponse est oui, alors on cree un signal.

Dans la strategie presente ici :

- on entre en position quand le prix semble trop bas par rapport a sa moyenne
- on sort quand le prix revient vers un niveau plus normal

L'idee est donc tres simple a raconter :

**on detecte un exces, puis on teste si le marche corrige cet exces.**

## Le backtest

C'est la partie centrale du projet.

Un backtest consiste a appliquer la strategie sur des donnees passees pour voir comment elle aurait reagi.

Ici, le moteur de backtest utilise `vectorbt`.  
Son role est de prendre :

- les prix
- les signaux d'entree
- les signaux de sortie
- les frais
- le capital de depart

et de reconstruire automatiquement ce qu'aurait donne la strategie.

Le backtest permet ensuite de calculer plusieurs mesures importantes :

- le rendement total
- la performance annualisee
- le ratio de Sharpe
- le drawdown maximal
- le taux de trades gagnants
- le nombre de trades

Autrement dit, on ne reste pas au niveau de l'intuition.  
On force l'idee a passer une premiere epreuve quantitative.

## Pourquoi cette etape est importante

Dans la finance comme dans beaucoup d'autres domaines, une idee peut sembler tres convaincante tant qu'on ne l'a pas testee.

Le backtest sert justement a eviter cela.

Il oblige a repondre a des questions concretes :

- la strategie gagne-t-elle vraiment ?
- combien de temps reste-t-elle en perte ?
- subit-elle de fortes baisses ?
- fonctionne-t-elle sur plusieurs cryptomonnaies ou seulement sur quelques cas particuliers ?

Le projet permet donc de passer :

**d'une intuition statistique a une evaluation mesuree.**

## L'analyse des resultats

Une fois le backtest termine, le travail n'est pas fini.

Les resultats doivent encore etre interpretes.

Le projet comporte donc une phase d'analyse qui produit :

- un resume des performances
- un fichier CSV exploitable
- un fichier JSON de synthese
- des graphiques comme les equity curves

Les equity curves sont particulierement utiles parce qu'elles montrent l'evolution de la valeur du portefeuille dans le temps.

Elles permettent de voir si la strategie :

- progresse de facon reguliere
- traverse de fortes periodes de baisse
- depend de quelques trades seulement

Cette partie est importante car une strategie peut afficher un bon rendement final tout en etant tres instable en cours de route.

## Ce que raconte vraiment le projet

Au fond, ce projet raconte une histoire simple :

1. on part d'une question de marche
2. on cherche un indicateur simple pour y repondre
3. on le transforme en regles claires
4. on teste ces regles sur des donnees historiques
5. on mesure objectivement ce que cela donne

Le coeur du projet n'est donc pas seulement le trading.

Le coeur du projet, c'est la methode :

**observer, mesurer, formaliser, tester, analyser.**

## Ce que l'on peut retenir

Si je devais resumer le projet en une phrase, je dirais :

**ce projet montre comment un concept statistique simple, le z-score, peut devenir une strategie quantitative complete allant de la donnee brute jusqu'au backtest et a l'analyse de performance.**

## Ouverture rapide vers la cyber

On peut aussi regarder ce sujet sous un angle plus cyber.

Pourquoi ?

Parce qu'au fond, la logique est proche de nombreux problemes de cybersécurité :

- on observe un systeme
- on collecte des donnees
- on cherche des comportements anormaux
- on transforme ces anomalies en signaux exploitables

Dans le trading, on cherche des anomalies de prix.  
En cyber, on cherche par exemple :

- des anomalies reseau
- des comportements utilisateurs inhabituels
- des pics d'activite suspects
- des deviations par rapport a une base normale

Le z-score peut donc etre vu non seulement comme un outil de trading quantitatif, mais aussi comme une premiere porte d'entree vers la **detection d'anomalies** en general.

Autrement dit :

**ce projet parle de finance quantitative, mais il touche aussi a une logique tres proche de la cyber : detecter ce qui sort de la normale dans un flux de donnees.**
