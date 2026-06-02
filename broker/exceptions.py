from __future__ import annotations


class BrokerError(Exception):
    """Erreur generique cote broker. Toutes les exceptions natives (ccxt, ib_insync,
    requests) sont wrappees dans une sous-classe de celle-ci."""


class AuthError(BrokerError):
    """Credentials manquants, invalides ou token expire."""


class BrokerConnectionError(BrokerError):
    """Impossible de joindre le broker (TWS down, reseau, endpoint indisponible)."""


class InsufficientFunds(BrokerError):
    """Solde insuffisant pour passer l'ordre."""


class InvalidOrder(BrokerError):
    """Ordre rejete : qty < min, prix hors bande, AssetType incoherent, etc."""


class OrderNotFound(BrokerError):
    """L'order_id n'existe pas (cancel/lookup sur un id inconnu)."""


class SymbolNotFound(BrokerError):
    """Le symbole n'est pas resolu chez ce broker."""


class RateLimited(BrokerError):
    """Quota d'appels API depasse."""
