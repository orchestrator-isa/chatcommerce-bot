#!/bin/bash

# Café Al Hizam Al Akhdar
CLIENT="bc23e515-5436-4053-a47d-60f75f0ba270"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Tajine de Pollo\",\"precio\":85}"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Cuscús\",\"precio\":70}"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Pastela\",\"precio\":60}"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Té Moruno\",\"precio\":10}"

# Café Al Amal
CLIENT="b2e5f80b-de48-4a8e-8af2-6d2943e5fee4"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Desayuno Completo\",\"precio\":45}"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Bocadillo Tortilla\",\"precio\":25}"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Café con Leche\",\"precio\":12}"

# Restaurant Dar Dmana
CLIENT="38ca393c-8c26-4fa4-87ab-0044c459c43b"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Tajine de Cordero\",\"precio\":120}"
curl -X POST https://chatcommerce-bot.onrender.com/api/platos -H "Content-Type: application/json" -d "{\"client_id\":\"$CLIENT\",\"nombre\":\"Calamar Plancha\",\"precio\":95}"

echo "✅ Menús cargados"
