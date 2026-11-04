# Empório da Música Store Assistant

A customer-facing virtual assistant for Empório da Música, a musical instrument store in Campo Grande (MS), that answers customers over the store's WhatsApp text channel on behalf of the support team.

## Sources of truth

**Fact**:
A verifiable piece of store data held in the system: a Product, its price, its Availability, a Promotion, or an Order. The Assistant only states a Fact it has just retrieved from the system.
_Avoid_: fact table, fact (in the dimensional-modeling sense)

**Policy**:
A rule or procedure from the store's internal manual, such as exchanges, returns, opening hours, payment, shipping, warranty, or privacy. Policies never establish prices, Availability, or Promotions.
_Avoid_: FAQ, rule base

**Wrong Price**:
A money amount the Assistant states that contradicts a Fact, such as a Product price, Promotional Price, or Order Total different from the system's, or an amount the Customer claimed. Leaving out a price is an incomplete answer, not a Wrong Price.
_Avoid_: missing price, price error

## Catalog

**Product**:
A musical instrument in the store's catalog, belonging to one category.
_Avoid_: item, SKU

**Accessory**:
Strings, cables, picks, pedals, amplifiers, cases, and similar goods. The store does not sell Accessories, so requests for them are out of scope.

**Availability**:
Whether a Product can be bought right now: available or temporarily unavailable. Never expressed to the customer as a stock count.
_Avoid_: stock level, inventory

**Price Range**:
The minimum price, maximum price, or both that a Customer states when looking for Products. It is the Customer's search limit, not a Fact, and is never presented as the price of a Product.
_Avoid_: budget, price cap, price ceiling

**Discontinued Product**:
A Product that is no longer part of the catalog. The Assistant says so and offers equivalent available Products.

**Coming-soon Product**:
A Product announced but not yet on sale. No price is promised for it.

**Promotion**:
A percentage discount on a single Product. Inactive Promotions are history and are never offered.
_Avoid_: deal, offer

**Active Promotion**:
A Promotion that the system marks as currently in force.

**List Price**:
A Product's price without any Promotion. Whenever the Assistant states a Promotional Price, she states the List Price with it.
_Avoid_: original price, full price

**Promotional Price**:
The List Price after the Active Promotion's percentage discount.
_Avoid_: sale price, discounted price

**Unstocked Category**:
A category the manual says the store sells but that currently has no Products in the system. The Assistant says it is temporarily out of stock, not that the store stopped selling it.
_Avoid_: discontinued category

## Orders

**Customer**:
A person who places Orders with the store.
_Avoid_: client, user, buyer

**Order**:
A Customer's purchase, identified by its order number and carrying a status: pending, confirmed, shipped, delivered, or cancelled.
_Avoid_: purchase, sale

**Order Total**:
The amount recorded on the Order. It is authoritative even when it differs from the sum of the Order's items at current prices.

**Data Anomaly**:
An internal inconsistency among Facts, or between Facts and Policies, such as a delivered Order containing a Coming-soon Product. Data Anomalies are kept as-is and only an Attendant resolves them.
_Avoid_: data error, bug

## Service

**Assistant**:
Lia, the store's virtual assistant that talks to Customers on the WhatsApp channel and always presents herself as a virtual assistant.
_Avoid_: bot, chatbot

**Attendant**:
A human member of the store's support team.
_Avoid_: operator, agent

**Handoff**:
Transferring a conversation to an Attendant, who continues the service from that point.
_Avoid_: escalation, transfer
