import sqlite3
import os
import webbrowser
import csv
import io
from flask import Flask, render_template_string, request, redirect, url_for, flash, session, make_response
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

# =====================================================================
# CONFIGURATION DE SÉCURITÉ
# =====================================================================
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "systeme_gestion_scolaire_secret_key_2026_secure_hash")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,  
    SESSION_COOKIE_SAMESITE='Lax',  
    PERMANENT_SESSION_LIFETIME=1800 
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Si l'application tourne sur Render avec un disque monté dans /var/data, on utilise ce dossier permanent
DATA_DIR = "/var/data" if os.path.exists("/var/data") else BASE_DIR
DB_NAME = os.path.join(DATA_DIR, "gestion.db")

# =====================================================================
# INITIALISATION DE LA BASE DE DONNÉES
# =====================================================================
def init_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON;")
        
        # Table de configuration pour stocker le titre personnalisé
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS config_site (
            cle TEXT PRIMARY KEY,
            valeur TEXT NOT NULL
        )""")
        
        # Table des sessions temporaires (Mois / Trimestre)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions_eval (
            id_session INTEGER PRIMARY KEY AUTOINCREMENT,
            nom_session TEXT NOT NULL,
            statut TEXT DEFAULT 'ACTIVE'
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS classes (
            id_classe INTEGER PRIMARY KEY AUTOINCREMENT,
            nom_classe TEXT NOT NULL,
            niveau TEXT NOT NULL
        )""")
        
        # Table élèves intégrant le session_id
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS eleves (
            id_eleve INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            prenom TEXT NOT NULL,
            date_naissance TEXT,
            classe_id INTEGER,
            session_id INTEGER,
            FOREIGN KEY (classe_id) REFERENCES classes(id_classe) ON DELETE SET NULL,
            FOREIGN KEY (session_id) REFERENCES sessions_eval(id_session) ON DELETE CASCADE
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS enseignants (
            id_ens INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            prenom TEXT NOT NULL,
            matiere TEXT NOT NULL,
            username TEXT UNIQUE DEFAULT NULL,
            password_hash TEXT DEFAULT NULL
        )""")

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cours (
            id_cours INTEGER PRIMARY KEY AUTOINCREMENT,
            id_classe INTEGER,
            id_enseignant INTEGER,
            matiere TEXT NOT NULL,
            coefficient INTEGER DEFAULT 1,
            FOREIGN KEY (id_classe) REFERENCES classes(id_classe) ON DELETE CASCADE,
            FOREIGN KEY (id_enseignant) REFERENCES enseignants(id_ens) ON DELETE CASCADE
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS notes (
            id_note INTEGER PRIMARY KEY AUTOINCREMENT,
            id_eleve INTEGER,
            id_cours INTEGER,
            note REAL NOT NULL CHECK(note >= 0 AND note <= 20),
            date TEXT,
            FOREIGN KEY (id_eleve) REFERENCES eleves(id_eleve) ON DELETE CASCADE,
            FOREIGN KEY (id_cours) REFERENCES cours(id_cours) ON DELETE CASCADE
        )""")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS admin_account (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL
        )""")
        
        conn.commit()
        
        # Titre par défaut dans la configuration
        cursor.execute("SELECT COUNT(*) FROM config_site WHERE cle = 'titre_onglets'")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO config_site VALUES ('titre_onglets', '📁 Choisissez l''onglet du mois / trimestre à consulter :')")
            conn.commit()
        
        # Compte admin par défaut
        cursor.execute("SELECT COUNT(*) FROM admin_account")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO admin_account VALUES (?, ?)", ("admin", generate_password_hash("admin123")))
            conn.commit()

        # Première session si vide
        cursor.execute("SELECT COUNT(*) FROM sessions_eval")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO sessions_eval (nom_session, statut) VALUES (?, ?)", ("Juin 2026", "ACTIVE"))
            conn.commit()

        # Insertion des données de démonstration initiales si vide
        cursor.execute("SELECT COUNT(*) FROM classes")
        if cursor.fetchone()[0] == 0:
            cursor.executemany("INSERT INTO classes (nom_classe, niveau) VALUES (?, ?)", [
                ("Licence 2 - Énergie Solaire", "L2"),
                ("Licence 2 - Option B", "L2")
            ])
            cursor.executemany("INSERT INTO eleves (nom, prenom, date_naissance, classe_id, session_id) VALUES (?, ?, ?, ?, 1)", [
                ("TRAORE", "Mariam", "12/05/2004", 1),
                ("DIARRA", "Adama", "23/09/2003", 1),
                ("COULIBALY", "Oumar", "05/11/2004", 1)
            ])
            cursor.execute("INSERT INTO enseignants (id_ens, nom, prenom, matiere, username, password_hash) VALUES (1, 'KONE', 'Ibrahim', 'Mathématiques', 'prof_math', ?)", (generate_password_hash("math123"),))
            cursor.execute("INSERT INTO enseignants (id_ens, nom, prenom, matiere, username, password_hash) VALUES (2, 'SANOGO', 'Awa', 'Biologie', 'prof_bio', ?)", (generate_password_hash("bio123"),))
            cursor.execute("INSERT INTO cours (id_cours, id_classe, id_enseignant, matiere, coefficient) VALUES (1, 1, 1, 'Mathématiques', 3)")
            cursor.execute("INSERT INTO cours (id_cours, id_classe, id_enseignant, matiere, coefficient) VALUES (2, 1, 2, 'Biologie', 2)")
            cursor.executemany("INSERT INTO notes (id_eleve, id_cours, note, date) VALUES (?, ?, ?, ?)", [
                (1, 1, 16.0, "01/06/2026"), (1, 2, 14.0, "01/06/2026"),
                (2, 1, 11.5, "01/06/2026"), (2, 2, 08.0, "01/06/2026"),
                (3, 1, 09.0, "01/06/2026"), (3, 2, 12.5, "01/06/2026")
            ])
            conn.commit()
        conn.close()
    except sqlite3.Error as e:
        print(f"Erreur d'initialisation de la base : {e}")

# =====================================================================
# LOGIQUE ET MOTEUR DE CALCULS
# =====================================================================
class GestionScolaireEngine:
    def get_conn(self):
        conn = sqlite3.connect(DB_NAME)
        conn.cursor().execute("PRAGMA foreign_keys = ON;")
        return conn

    def obtenir_session_active(self):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id_session, nom_session FROM sessions_eval WHERE statut = 'ACTIVE' ORDER BY id_session DESC LIMIT 1")
        res = cursor.fetchone()
        conn.close()
        return res if res else (None, "Aucune session active")

    def obtenir_toutes_les_sessions(self):
        conn = self.get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT id_session, nom_session, statut FROM sessions_eval ORDER BY id_session DESC")
        res = cursor.fetchall()
        conn.close()
        return res

    def ajouter_eleve(self, nom, prenom, date_n, classe_id):
        try:
            id_sess, _ = self.obtenir_session_active()
            if not id_sess:
                return False, "Impossible d'inscrire un élève sans session active."
                
            conn = self.get_conn()
            cursor = conn.cursor()
            c_id = int(classe_id) if classe_id and str(classe_id).isdigit() else None
            cursor.execute("INSERT INTO eleves (nom, prenom, date_naissance, classe_id, session_id) VALUES (?, ?, ?, ?, ?)",
                           (nom.strip().upper(), prenom.strip(), date_n.strip(), c_id, id_sess))
            id_nouvel_eleve = cursor.lastrowid
            if c_id:
                cursor.execute("SELECT id_cours FROM cours WHERE id_classe = ?", (c_id,))
                for crs in cursor.fetchall():
                    cursor.execute("INSERT INTO notes (id_eleve, id_cours, note, date) VALUES (?, ?, 0.0, '01/07/2026')", (id_nouvel_eleve, crs[0]))
            conn.commit()
            conn.close()
            return True, "L'élève a été inscrit avec succès."
        except sqlite3.Error:
            return False, "Erreur lors de l'enregistrement de l'élève."

    def ajouter_classe(self, nom_classe, niveau):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("INSERT INTO classes (nom_classe, niveau) VALUES (?, ?)", (nom_classe.strip(), niveau.strip()))
            conn.commit()
            conn.close()
            return True, "Classe créée avec succès."
        except sqlite3.Error:
            return False, "Erreur de création de la classe."

    def supprimer_classe(self, id_classe):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM classes WHERE id_classe=?", (int(id_classe),))
            conn.commit()
            conn.close()
            return True, "La classe a été supprimée."
        except sqlite3.Error:
            return False, "Erreur de suppression."

    def ajouter_enseignant(self, nom, prenom, matiere, username, password):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            pw_hash = generate_password_hash(password)
            cursor.execute("INSERT INTO enseignants (nom, prenom, matiere, username, password_hash) VALUES (?, ?, ?, ?, ?)",
                           (nom.strip().upper(), prenom.strip(), matiere.strip(), username.strip(), pw_hash))
            conn.commit()
            conn.close()
            return True, f"L'enseignant {prenom} {nom.upper()} a été ajouté."
        except sqlite3.IntegrityError:
            return False, "Cet identifiant de connexion est déjà utilisé."
        except sqlite3.Error:
            return False, "Erreur lors de l'ajout de l'enseignant."

    def supprimer_enseignant(self, id_ens):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM enseignants WHERE id_ens=?", (int(id_ens),))
            conn.commit()
            conn.close()
            return True, "Enseignant retiré du système."
        except sqlite3.Error:
            return False, "Erreur de suppression de l'enseignant."

    def ajouter_cours(self, id_classe, id_enseignant, matiere, coefficient):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            coef = int(coefficient) if coefficient and str(coefficient).isdigit() else 1
            cursor.execute("INSERT INTO cours (id_classe, id_enseignant, matiere, coefficient) VALUES (?, ?, ?, ?)", 
                           (int(id_classe), int(id_enseignant), matiere.strip(), coef))
            id_nouveau_cours = cursor.lastrowid
            
            id_sess, _ = self.obtenir_session_active()
            cursor.execute("SELECT id_eleve FROM eleves WHERE classe_id = ? AND session_id = ?", (int(id_classe), id_sess))
            for el in cursor.fetchall():
                cursor.execute("INSERT INTO notes (id_eleve, id_cours, note, date) VALUES (?, ?, 0.0, '01/07/2026')", (el[0], id_nouveau_cours))
            conn.commit()
            conn.close()
            return True, f"Matière assignée avec succès."
        except sqlite3.Error:
            return False, "Erreur lors de l'ajout de la matière."

    def modifier_note(self, id_note, nouvelle_note, current_user_role, current_user_id):
        try:
            valeur_note = float(nouvelle_note)
            if not (0 <= valeur_note <= 20):
                return False, "La note doit être comprise entre 0 et 20."
            conn = self.get_conn()
            cursor = conn.cursor()
            
            if current_user_role == "ENSEIGNANT":
                cursor.execute("""
                    SELECT COUNT(*) FROM notes n JOIN cours c ON n.id_cours = c.id_cours
                    WHERE n.id_note = ? AND c.id_enseignant = ?
                """, (int(id_note), int(current_user_id)))
                if cursor.fetchone()[0] == 0:
                    conn.close()
                    return False, "🔒 Accès refusé : Vous n'êtes pas l'enseignant en charge de cette matière."
                    
            cursor.execute("UPDATE notes SET note=? WHERE id_note=?", (valeur_note, int(id_note)))
            conn.commit()
            conn.close()
            return True, "Note mise à jour avec succès."
        except (sqlite3.Error, ValueError):
            return False, "Donnée numérique incorrecte."

    def obtenir_classement_par_session(self, id_classe, id_session):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT id_eleve, nom, prenom FROM eleves WHERE classe_id=? AND session_id=?", (int(id_classe), int(id_session)))
            eleves = cursor.fetchall()
            liste_classement = []
            for id_el, nom, prenom in eleves:
                cursor.execute("SELECT n.note, c.coefficient FROM notes n JOIN cours c ON n.id_cours = c.id_cours WHERE n.id_eleve = ?", (id_el,))
                total_points, total_coefficients = 0.0, 0
                for note, coef in cursor.fetchall():
                    total_points += (note * coef)
                    total_coefficients += coef
                moy = round(total_points / total_coefficients, 2) if total_coefficients > 0 else 0.0
                liste_classement.append((id_el, f"{prenom} {nom}", moy))
            liste_classement.sort(key=lambda x: x[2], reverse=True)
            conn.close()
            return liste_classement, None
        except sqlite3.Error:
            return None, "Erreur lors du calcul des moyennes."

    def basculer_nouvelle_session(self, nouveau_nom):
        try:
            conn = self.get_conn()
            cursor = conn.cursor()
            
            cursor.execute("SELECT id_session FROM sessions_eval WHERE statut = 'ACTIVE' ORDER BY id_session DESC LIMIT 1")
            ancienne_session_res = cursor.fetchone()
            
            # On passe toutes les anciennes sessions en statut ARCHIVEE
            cursor.execute("UPDATE sessions_eval SET statut = 'ARCHIVEE'")
            
            # Création de la nouvelle session active
            cursor.execute("INSERT INTO sessions_eval (nom_session, statut) VALUES (?, 'ACTIVE')", (nouveau_nom,))
            nouvelle_session_id = cursor.lastrowid
            
            # Si une session précédente existait, on transfère les mêmes profils d'élèves sans toucher à l'ancienne
            if ancienne_session_res:
                ancienne_session_id = ancienne_session_res[0]
                cursor.execute("SELECT nom, prenom, date_naissance, classe_id FROM eleves WHERE session_id = ?", (ancienne_session_id,))
                eleves_a_conserver = cursor.fetchall()
                
                for nom, prenom, date_n, classe_id in eleves_a_conserver:
                    cursor.execute("""
                        INSERT INTO eleves (nom, prenom, date_naissance, classe_id, session_id) 
                        VALUES (?, ?, ?, ?, ?)
                    """, (nom, prenom, date_n, classe_id, nouvelle_session_id))
                    nouvel_id_eleve = cursor.lastrowid
                    
                    if classe_id:
                        cursor.execute("SELECT id_cours FROM cours WHERE id_classe = ?", (classe_id,))
                        for crs in cursor.fetchall():
                            cursor.execute("""
                                INSERT INTO notes (id_eleve, id_cours, note, date) 
                                VALUES (?, ?, 0.0, '01/07/2026')
                            """, (nouvel_id_eleve, crs[0]))
                            
            conn.commit()
            conn.close()
            return True, f"Session '{nouveau_nom}' initialisée. Vos élèves et professeurs sont reconduits."
        except sqlite3.Error as e:
            print(e)
            return False, "Erreur lors de la reconduction du cycle."

engine = GestionScolaireEngine()

# =====================================================================
# INTERFACES UTILISATEURS (TAILWIND HTML)
# =====================================================================

BASE_LAYOUT = """
<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Système Académique Centralisé</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 font-sans text-slate-800">
    <nav class="bg-slate-900 text-white shadow-xl">
        <div class="max-w-6xl mx-auto px-4 py-4 flex justify-between items-center">
            <div class="flex items-center space-x-3">
                <span class="text-2xl">🛡️</span>
                <a href="/" class="text-xl font-bold tracking-wide">
                    {% if session.get('user_role') == 'ADMIN' %} <span class="text-amber-400">(L.P.M.C.T) </span>
                    {% elif session.get('user_role') == 'ENSEIGNANT' %} <span class="text-emerald-400">ESPACE ENSEIGNANT</span>
                    {% else %} <span class="text-blue-400">ESPACE ÉLÈVE</span> {% endif %}
                </a>
            </div>
            <div class="space-x-4 text-sm font-semibold flex items-center">
                {% if session.get('user_role') in ['ADMIN', 'ENSEIGNANT'] %}
                    <a href="/" class="hover:text-amber-300 transition">Classements & Notes</a>
                    {% if session.get('user_role') == 'ADMIN' %}
                        <a href="/classes" class="hover:text-amber-300 transition">Structure, Profs & Sessions</a>
                        <a href="/sauvegardes" class="text-cyan-400 hover:text-cyan-300 transition">💾 Sauvegardes</a>
                        <a href="/ajouter-eleve" class="bg-amber-500 text-slate-950 px-3 py-1.5 rounded-lg hover:bg-amber-400 transition">Inscrire Élève</a>
                    {% endif %}
                {% endif %}
                {% if session.get('username') %}
                <span class="text-xs px-2 py-0.5 rounded bg-white/10 text-slate-300 font-mono">{{ session.get('username') | e }}</span>
                <a href="/logout" class="text-rose-400 hover:text-rose-300 text-xs underline">Déconnexion</a>
                {% endif %}
            </div>
        </div>
    </nav>

    <div class="max-w-6xl mx-auto px-4 pt-4" id="flash-container">
        {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
                {% for category, message in messages %}
                    <div class="p-4 rounded-lg font-medium shadow-sm transition opacity duration-500 {% if category == 'success' %}bg-emerald-100 text-emerald-800 border border-emerald-200{% elif category == 'info' %}bg-blue-100 text-blue-800 border border-blue-200{% elif category == 'warning' %}bg-amber-100 text-amber-800 border border-amber-200{% else %}bg-rose-100 text-rose-800 border border-rose-200{% endif %}">
                        {{ message | safe }}
                    </div>
                {% endfor %}
            {% endif %}
        {% endwith %}
    </div>

    <main class="max-w-6xl mx-auto px-4 py-6">
        {{ MainContent | safe }}
    </main>
</body>
</html>
"""

# TEXT MODIFIÉ ICI : "Modifier le texte" est remplacé par "Réinitialiser"
INDEX_CONTENT = """
{% if session.get('user_role') == 'ADMIN' %}
<div class="mb-6 bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
    <div class="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 mb-3">
        <p class="text-xs font-bold uppercase tracking-wider text-slate-500">{{ titre_onglets | e }}</p>
        
        <form action="/modifier-titre-onglets" method="POST" class="flex gap-2 items-center w-full sm:w-auto">
            <input type="text" name="nouveau_titre" value="{{ titre_onglets | e }}" required 
                   class="border bg-slate-50 rounded-lg px-2 py-1 text-xs font-medium focus:outline-none text-slate-700 w-full sm:w-64">
            <button type="submit" class="bg-blue-600 text-white font-bold px-3 py-1 rounded-lg text-xs hover:bg-blue-700 transition shrink-0">📝 Réinitialiser</button>
        </form>
    </div>
    
    <div class="flex flex-wrap gap-2 border-b border-slate-100 pb-2">
        {% for sess in toutes_sessions %}
            <a href="/?session_visualisee_id={{ sess[0] }}{% if classe_selectionnee %}&classe_id={{ classe_selectionnee }}{% endif %}" 
               class="px-4 py-2 rounded-t-lg font-bold text-sm tracking-wide border-t border-x transition {% if session_visualisee_id == sess[0] %} bg-blue-600 text-white border-blue-600 shadow-md {% else %} bg-white text-slate-700 hover:bg-slate-100 border-slate-200 {% endif %}">
               📂 {{ sess[1] | e }} {% if sess[2] == 'ACTIVE' %}<span class="ml-1 text-xs bg-emerald-500 text-white px-1.5 py-0.5 rounded font-black">ACTUEL</span>{% endif %}
            </a>
        {% endfor %}
    </div>
</div>
{% endif %}

<div class="mb-6 p-4 bg-slate-800 text-white rounded-xl shadow-sm flex justify-between items-center">
    <div>
        <p class="text-xs font-bold uppercase tracking-wider text-amber-400">Période observée</p>
        <h2 class="text-lg font-black font-mono">🔍 {{ nom_session_visualisee }}</h2>
    </div>
    {% if est_session_historique %}
        <span class="px-3 py-1 bg-amber-500 text-slate-950 text-xs font-bold rounded-full">📄 CONSULTATION HISTORIQUE (ARCHIVE)</span>
    {% else %}
        <span class="px-3 py-1 bg-emerald-500 text-white text-xs font-bold rounded-full">✍️ SESSION EN COURS DE SAISIE</span>
    {% endif %}
</div>

<div class="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
    <div class="bg-white p-6 rounded-xl border shadow-sm">
        <h3 class="text-slate-400 text-xs font-bold uppercase">Nombre de Salle  (classe) </h3>
        <p class="text-3xl font-black mt-1 text-slate-900">{{ stats.total_classes }}</p>
    </div>
    <div class="bg-white p-6 rounded-xl border shadow-sm">
        <h3 class="text-slate-400 text-xs font-bold uppercase">Nombre d'Élèves total</h3>
        <p class="text-3xl font-black mt-1 text-slate-900">{{ stats.total_eleves }}</p>
    </div>
    <div class="bg-white p-6 rounded-xl border shadow-sm">
        <h3 class="text-slate-400 text-xs font-bold uppercase">Taux Réussite en % </h3>
        <p class="text-3xl font-black mt-1 text-emerald-600">{{ stats.taux_reussite }}%</p>
    </div>
</div>

<div class="bg-white p-6 rounded-xl border shadow-sm mb-8">
    <h2 class="text-sm font-bold uppercase text-slate-500 mb-3">Sélectionner la classe à observer :</h2>
    <form action="/" method="GET" class="flex gap-4 items-center max-w-md">
        <input type="hidden" name="session_visualisee_id" value="{{ session_visualisee_id }}">
        <select name="classe_id" class="w-full bg-slate-50 border rounded-lg p-2.5 text-sm font-semibold text-slate-900 focus:outline-none">
            <option value="">-- Choisir la classe --</option>
            {% for cl in classes %}
                <option value="{{ cl[0] }}" {% if classe_selectionnee == cl[0] %}selected{% endif %}>{{ cl[1] | e }} ({{ cl[2] | e }})</option>
            {% endfor %}
        </select>
        <button type="submit" class="bg-slate-900 text-white px-6 py-2.5 rounded-lg font-bold text-sm hover:bg-slate-800 transition">Afficher</button>
    </form>
</div>

{% if classe_selectionnee %}
<div class="bg-white rounded-xl border shadow-sm overflow-hidden mb-8">
    <div class="p-5 border-b bg-slate-50/50">
        <h2 class="font-black text-slate-800 uppercase tracking-wide text-sm">📋 Rang et moyennes </h2>
    </div>
    <table class="w-full text-sm text-left text-slate-500">
        <thead class="bg-slate-100 text-xs text-slate-700 uppercase">
            <tr>
                <th class="px-6 py-3.5">Rang</th>
                <th class="px-6 py-3.5">Nom & Prénom</th>
                <th class="px-6 py-3.5 text-center">Moyenne Période</th>
            </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
            {% for rang, id_el, nom_complet, moy in classement %}
            <tr class="bg-white hover:bg-slate-50/50 transition">
                <td class="px-6 py-4 font-black text-blue-600">{{ rang }}e</td>
                <td class="px-6 py-4 font-bold text-slate-900">
                    <a href="/bulletin/{{ id_el }}?session_id={{ session_visualisee_id }}" class="text-blue-600 hover:text-blue-800 hover:underline">🎓 {{ nom_complet | e }}</a>
                </td>
                <td class="px-6 py-4 text-center">
                    <span class="px-2.5 py-1 rounded-md font-black text-xs {% if moy >= 10 %}bg-emerald-50 text-emerald-700{% else %}bg-rose-50 text-rose-700{% endif %}">
                        {{ moy }} / 20
                    </span>
                </td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>

<div class="bg-white rounded-xl border shadow-sm overflow-hidden mb-8">
    <div class="p-5 border-b bg-slate-50/50">
        <h2 class="font-black text-emerald-800 uppercase tracking-wide text-sm">🎯 Cahier de Notes {% if est_session_historique %}(Mode Lecture Seule 🔒){% else %}(Modifiable ✍️){% endif %}</h2>
    </div>
    {% if notes_classe %}
    <table class="w-full text-sm text-left text-slate-500">
        <thead class="bg-slate-100 text-xs text-slate-700 uppercase">
            <tr>
                <th class="px-6 py-3.5">Élève</th>
                <th class="px-6 py-3.5">Matière</th>
                <th class="px-6 py-3.5 text-center">Note Enregistrée</th>
                {% if not est_session_historique %}
                <th class="px-6 py-3.5 text-right">Modifier la Note</th>
                {% endif %}
            </tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
            {% for id_note, eleve_nom, matiere, coef, note in notes_classe %}
            <tr class="bg-white hover:bg-slate-50/50 transition">
                <td class="px-6 py-4 text-slate-900 font-bold">{{ eleve_nom | e }}</td>
                <td class="px-6 py-4 text-slate-600 font-medium">{{ matiere | e }} <span class="text-xs text-slate-400">(Coef {{coef}})</span></td>
                <td class="px-6 py-4 text-center font-black text-slate-900">{{ note }}</td>
                
                {% if not est_session_historique %}
                <td class="px-6 py-4 text-right">
                    <form action="/modifier-note/{{ id_note }}" method="POST" class="inline-flex gap-2 items-center">
                        <input type="number" step="0.1" min="0" max="20" name="nouvelle_note" value="{{ note }}" required class="w-16 border rounded px-1.5 py-1 text-center font-bold text-sm">
                        <button type="submit" class="bg-emerald-600 text-white font-bold px-2.5 py-1 rounded text-xs hover:bg-emerald-700">Enregistrer</button>
                    </form>
                </td>
                {% endif %}
            </tr>
            {% endfor %}
        </tbody>
    </table>
    {% else %}
    <div class="p-6 text-center text-sm text-slate-500 bg-slate-50 font-medium">
        Aucune donnée d'évaluation enregistrée pour cette classe dans cet onglet.
    </div>
    {% endif %}
</div>
{% endif %}
"""

CLASSES_CONTENT = """
<div class="space-y-8">
    <div class="bg-white p-6 rounded-xl border shadow-sm">
        <h2 class="text-base font-black uppercase text-slate-800 mb-4 pb-2 border-b">📂 Configuration & Nettoyage des Onglets (Mois / Trimestres)</h2>
        
        <form action="/nouvelle-session" method="POST" class="flex gap-4 items-end mb-6 bg-slate-50 p-4 rounded-xl border">
            <div class="flex-1">
                <label class="block text-xs font-bold uppercase text-slate-600 mb-1">➕ Créer un nouvel onglet</label>
                <input type="text" name="nom_session" placeholder="Ex: Août 2026, Septembre..." required class="w-full p-2.5 border rounded-lg bg-white text-sm font-bold">
            </div>
            <button type="submit" class="bg-amber-600 text-white font-bold px-6 py-2.5 rounded-lg text-sm hover:bg-amber-700 transition">Initialiser nouveau  mois / trimestre </button>
        </form>

        <label class="block text-xs font-black uppercase text-slate-500 mb-2">📋 Éditer ou Supprimer les dossiers d'onglets existants :</label>
        <div class="overflow-hidden border rounded-xl">
            <table class="w-full text-sm text-left text-slate-500">
                <thead class="bg-slate-100 text-xs text-slate-700 uppercase font-bold">
                    <tr>
                        <th class="px-4 py-3">Statut</th>
                        <th class="px-4 py-3">Nom actuel du dossier</th>
                        <th class="px-4 py-3 text-right">Actions de modification</th>
                    </tr>
                </thead>
                <tbody class="divide-y divide-slate-100 bg-white">
                    {% for sess in sessions %}
                    <tr class="hover:bg-slate-50/50 transition">
                        <td class="px-4 py-3">
                            {% if sess[2] == 'ACTIVE' %}
                                <span class="bg-emerald-100 text-emerald-800 font-bold px-2 py-0.5 rounded text-xs">ACTIF ✍️</span>
                            {% else %}
                                <span class="bg-slate-100 text-slate-600 font-bold px-2 py-0.5 rounded text-xs">ARCHIVE 🔒</span>
                            {% endif %}
                        </td>
                        <td class="px-4 py-3">
                            <form action="/renommer-session/{{ sess[0] }}" method="POST" class="flex items-center gap-2">
                                <input type="text" name="nouveau_nom" value="{{ sess[1] | e }}" required class="border bg-slate-50 font-bold px-2 py-1 rounded text-sm w-48 focus:bg-white focus:outline-none">
                                <button type="submit" class="text-xs bg-slate-900 text-white font-medium px-2.5 py-1 rounded hover:bg-slate-800">💾 Renommer</button>
                            </form>
                        </td>
                        <td class="px-4 py-3 text-right">
                            <a href="/supprimer-session/{{ sess[0] }}" 
                               onclick="return confirm('⚠️ ATTENTION ! Supprimer cet onglet supprimera DEFINITIVEMENT toutes les notes associées à ce mois/trimestre. Continuer ?')" 
                               class="text-xs font-bold text-rose-600 hover:text-rose-900 border border-rose-200 px-2.5 py-1 rounded-lg bg-rose-50 hover:bg-rose-100 transition inline-block">
                               🗑️ Supprimer 
                            </a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div class="bg-white p-6 rounded-xl border shadow-sm">
            <h2 class="text-sm font-black uppercase text-slate-700 mb-4">🏫 Gérer les Classes</h2>
            <form action="/ajouter-classe" method="POST" class="space-y-4">
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Intitulé</label><input type="text" name="nom_classe" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm"></div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Niveau</label><input type="text" name="niveau" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm"></div>
                <button type="submit" class="w-full bg-slate-900 text-white font-bold py-2 rounded-lg text-xs">Valider</button>
            </form>
            <div class="mt-4 border-t pt-2 max-h-40 overflow-y-auto space-y-1">
                {% for cl in classes %}
                <div class="flex justify-between items-center text-xs p-1 bg-slate-50 rounded">
                    <span>{{ cl[1] }}</span>
                    <a href="/supprimer-classe/{{ cl[0] }}" onclick="return confirm('Supprimer ?')" class="text-rose-600">🗑️</a>
                </div>
                {% endfor %}
            </div>
        </div>

        <div class="bg-white p-6 rounded-xl border shadow-sm">
            <h2 class="text-sm font-black uppercase text-blue-800 mb-4">👤 Gérer les Comptes Professeurs</h2>
            <form action="/ajouter-prof" method="POST" class="space-y-3">
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-0.5">Nom</label><input type="text" name="nom" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm"></div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-0.5">Prénom</label><input type="text" name="prenom" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm"></div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-0.5">Matière Spécialité</label><input type="text" name="matiere" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm"></div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-0.5">Nom d'utilisateur</label><input type="text" name="username" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm font-mono"></div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-0.5">Mot de passe</label><input type="password" name="password" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm"></div>
                <button type="submit" class="w-full bg-blue-600 text-white font-bold py-2 rounded-lg text-xs hover:bg-blue-700">Enregistrer l'enseignant</button>
            </form>
        </div>

        <div class="bg-white p-6 rounded-xl border shadow-sm">
            <h2 class="text-sm font-black uppercase text-emerald-800 mb-4">📖 Programmes & Coefficients</h2>
            <form action="/ajouter-cours" method="POST" class="space-y-4">
                <div>
                    <label class="block text-xs font-bold uppercase text-slate-500 mb-1">Classe</label>
                    <select name="id_classe" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm">
                        {% for cl in classes %}<option value="{{ cl[0] }}">{{ cl[1] | e }}</option>{% endfor %}
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-bold uppercase text-slate-500 mb-1">Enseignant responsable</label>
                    <select name="id_enseignant" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm">
                        {% for pr in profs %}<option value="{{ pr[0] }}">{{ pr[2] | e }} {{ pr[1] | e }} ({{ pr[3] | e }})</option>{% endfor %}
                    </select>
                </div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Nom du cours</label><input type="text" name="matiere" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm"></div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Coefficient</label><input type="number" name="coefficient" value="1" min="1" required class="w-full p-2 border rounded-lg bg-slate-50 text-sm font-bold"></div>
                <button type="submit" class="w-full bg-emerald-600 text-white font-bold py-2 rounded-lg text-xs hover:bg-emerald-700">Lier le cours</button>
            </form>
        </div>
    </div>
</div>
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="fr">
<head><meta charset="UTF-8"><title>Authentification</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-slate-900 min-h-screen flex items-center justify-center p-4">
    <div class="w-full max-w-4xl bg-white rounded-3xl shadow-2xl overflow-hidden grid grid-cols-1 md:grid-cols-2">
        <div class="p-8 bg-slate-50 border-r flex flex-col justify-center">
            <div class="text-center mb-6">
                <span class="text-4xl">🎓</span>
                <h2 class="text-xl font-black text-slate-800 uppercase mt-2">Accès Étudiant</h2>
            </div>
            {% with messages = get_flashed_messages(category_filter=["eleve_err"]) %}
                {% if messages %}{% for msg in messages %}
                    <div class="mb-4 p-3 bg-rose-100 text-rose-700 rounded-xl text-center font-semibold text-xs">{{ msg | e }}</div>
                {% endfor %}{% endif %}
            {% endwith %}
            <form action="/login-eleve" method="POST" class="space-y-4">
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Nom</label><input type="text" name="nom" required class="w-full p-3 border rounded-xl uppercase font-bold"></div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Prénom</label><input type="text" name="prenom" required class="w-full p-3 border rounded-xl font-semibold"></div>
                <button type="submit" class="w-full bg-blue-600 text-white font-bold p-3 rounded-xl hover:bg-blue-700 text-sm transition">Se connecter</button>
            </form>
        </div>
        <div class="p-8 flex flex-col justify-center">
            <div class="text-center mb-6">
                <span class="text-4xl">🔒</span>
                <h2 class="text-xl font-black text-slate-800 uppercase mt-2">Espace Direction / Enseignants</h2>
            </div>
            {% with messages = get_flashed_messages(category_filter=["staff_err"]) %}
                {% if messages %}{% for msg in messages %}
                    <div class="mb-4 p-3 bg-rose-100 text-rose-700 rounded-xl text-center font-semibold text-xs">{{ msg | safe }}</div>
                {% endfor %}{% endif %}
            {% endwith %}
            <form action="/login" method="POST" class="space-y-4">
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Identifiant</label><input type="text" name="username" required class="w-full p-3 border rounded-xl font-mono"></div>
                <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Mot de passe</label><input type="password" name="password" required class="w-full p-3 border rounded-xl"></div>
                <button type="submit" class="w-full bg-slate-900 text-white font-bold p-3 rounded-xl hover:bg-slate-800 text-sm transition">S'authentifier</button>
            </form>
        </div>
    </div>
</body>
</html>
"""

FORM_ELEVE_CONTENT = """
<div class="max-w-xl mx-auto bg-white p-8 rounded-xl border shadow-sm">
    <h2 class="text-xl font-black text-slate-900 mb-6 uppercase border-b pb-2">Inscrire un Nouvel Élève</h2>
    <form method="POST" class="space-y-4">
        <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Nom</label><input type="text" name="nom" required class="w-full p-2.5 border rounded-lg bg-slate-50 uppercase text-sm font-bold"></div>
        <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Prénom</label><input type="text" name="prenom" required class="w-full p-2.5 border rounded-lg bg-slate-50 text-sm"></div>
        <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Date de naissance</label><input type="text" name="date_naissance" placeholder="JJ/MM/AAAA" class="w-full p-2.5 border rounded-lg bg-slate-50 text-sm"></div>
        <div><label class="block text-xs font-bold uppercase text-slate-500 mb-1">Classe</label>
            <select name="classe_id" class="w-full p-2.5 border rounded-lg bg-slate-50 text-sm">
                {% for cl in classes %}<option value="{{ cl[0] }}">{{ cl[1] | e }}</option>{% endfor %}
            </select>
        </div>
        <div class="pt-4 flex justify-between"><a href="/" class="text-sm text-slate-500 hover:underline">Annuler</a><button type="submit" class="bg-slate-900 text-white font-bold py-2.5 px-6 rounded-lg text-sm">Valider Inscription</button></div>
    </form>
</div>
"""

BULLETIN_CONTENT = """
<div class="max-w-3xl mx-auto bg-white rounded-2xl border shadow-xl overflow-hidden">
    <div class="bg-slate-900 text-white p-8">
        <h1 class="text-2xl font-black uppercase">{{ eleve[2] | e }} {{ eleve[1] | e }}</h1>
        <p class="text-xs text-slate-400">Période d'évaluation : {{ session_nom }} | Classe : {{ eleve[5] | e }}</p>
    </div>
    <div class="p-6">
        <table class="w-full text-sm text-left">
            <thead class="bg-slate-50 border-b text-xs uppercase">
                <tr><th>Matière</th><th class="text-center">Coefficient</th><th class="text-center">Note de la Session</th><th class="text-right">Points</th></tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
                {% for matiere, coef, note in details_notes %}
                <tr>
                    <td class="py-3 font-bold text-slate-800 uppercase text-xs">{{ matiere | e }}</td>
                    <td class="text-center">x{{ coef }}</td>
                    <td class="text-center font-mono font-bold">{{ note }}</td>
                    <td class="text-right font-mono">{{ (note * coef) | round(2) }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        <div class="mt-6 bg-slate-50 p-4 rounded-xl flex justify-between items-center border">
            <span class="text-xs font-bold text-slate-500 uppercase">Moyenne Générale sur cette Période :</span>
            <span class="text-2xl font-black text-slate-900">{{ moyenne_generale }} / 20</span>
        </div>
        <div class="mt-6 pt-4 border-t flex justify-between"><a href="javascript:history.back()" class="text-xs text-blue-600 font-bold">← Retour</a><button onclick="window.print()" class="bg-slate-900 text-white px-4 py-2 rounded text-xs font-bold">Imprimer</button></div>
    </div>
</div>
"""

SAUVEGARDES_CONTENT = """
<div class="max-w-3xl mx-auto space-y-6">
    <div class="bg-white p-6 rounded-xl border shadow-sm">
        <h2 class="text-lg font-black text-slate-900 mb-2 uppercase">💾 Exporter l'état des données</h2>
        <p class="text-xs text-slate-500 mb-6">Générez à tout moment des extractions propres pour consulter vos listes sous Excel.</p>
        
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div class="p-5 border rounded-xl bg-slate-50 flex flex-col justify-between">
                <div><span class="text-2xl">👤</span><h3 class="font-bold text-sm text-slate-800 mt-2">Liste des Enseignants</h3></div>
                <a href="/export/professeurs" class="mt-4 block text-center bg-blue-600 text-white font-bold py-2 px-4 rounded-lg text-xs hover:bg-blue-700 transition">Télécharger (.CSV)</a>
            </div>
            <div class="p-5 border rounded-xl bg-slate-50 flex flex-col justify-between">
                <div><span class="text-2xl">🎓</span><h3 class="font-bold text-sm text-slate-800 mt-2">Liste des Élèves (Tous cycles confondus)</h3></div>
                <a href="/export/eleves" class="mt-4 block text-center bg-emerald-600 text-white font-bold py-2 px-4 rounded-lg text-xs hover:bg-emerald-700 transition">Télécharger (.CSV)</a>
            </div>
        </div>
    </div>
</div>
"""

ELEVE_DASHBOARD_CONTENT = """
<div class="max-w-3xl mx-auto bg-white rounded-2xl border shadow-xl overflow-hidden">
    <div class="bg-slate-900 text-white p-6 border-b-4 border-blue-500">
        <h1 class="text-2xl font-black uppercase">👋 {{ eleve[2] | e }} {{ eleve[1] | e }}</h1>
        <p class="text-xs text-slate-400 mt-1">Classe : {{ eleve[5] | e }}</p>
    </div>
    <div class="p-6">
        <h2 class="text-sm font-bold text-slate-500 uppercase mb-4">📊 Relevé - Session active : <span class="text-blue-600 font-mono">{{ session_nom }}</span></h2>
        <table class="w-full text-sm text-left">
            <thead class="bg-slate-50 border-b text-xs uppercase">
                <tr><th class="px-4 py-3">Matière</th><th class="px-4 py-3 text-center">Coefficient</th><th class="px-4 py-3 text-center">Note</th></tr>
            </thead>
            <tbody class="divide-y divide-slate-100">
                {% for matiere, coef, note in details_notes %}
                <tr>
                    <td class="px-4 py-4 font-bold text-slate-800 uppercase text-xs">{{ matiere | e }}</td>
                    <td class="px-4 py-4 text-center">x{{ coef }}</td>
                    <td class="px-4 py-4 text-center font-mono font-bold {% if note >= 10 %}text-emerald-600{% else %}text-rose-600{% endif %}">{{ note }} / 20</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
</div>
"""

# =====================================================================
# SYSTEME DE ROUTAGE FLASK
# =====================================================================

@app.route("/login-eleve", methods=["POST"])
def route_login_eleve():
    nom = request.form.get("nom", "").strip().upper()
    prenom = request.form.get("prenom", "").strip()
    id_sess, _ = engine.obtenir_session_active()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.id_eleve, e.nom, e.prenom, e.date_naissance, e.classe_id, c.nom_classe 
        FROM eleves e LEFT JOIN classes c ON e.classe_id = c.id_classe 
        WHERE UPPER(e.nom) = ? AND e.prenom = ? AND e.session_id = ?
    """, (nom, prenom, id_sess))
    eleve = cursor.fetchone()
    conn.close()
    if eleve:
        session.clear()
        session["username"] = f"{eleve[2]} {eleve[1]}"
        session["user_role"] = "ELEVE"
        session["id_eleve"] = eleve[0]
        return redirect(url_for("route_espace_eleve"))
    flash("Profil introuvable pour la session active.", "eleve_err")
    return redirect(url_for("route_login"))

@app.route("/espace-eleve")
def route_espace_eleve():
    if session.get("user_role") != "ELEVE": return redirect(url_for("route_login"))
    id_eleve = session.get("id_eleve")
    _, session_nom = engine.obtenir_session_active()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.id_eleve, e.nom, e.prenom, e.date_naissance, e.classe_id, c.nom_classe 
        FROM eleves e LEFT JOIN classes c ON e.classe_id = c.id_classe WHERE e.id_eleve = ?
    """, (id_eleve,))
    eleve = cursor.fetchone()
    cursor.execute("SELECT co.matiere, co.coefficient, n.note FROM notes n JOIN cours co ON n.id_cours = co.id_cours WHERE n.id_eleve = ?", (id_eleve,))
    details_notes = cursor.fetchall()
    conn.close()
    return render_template_string(BASE_LAYOUT, MainContent=render_template_string(ELEVE_DASHBOARD_CONTENT, eleve=eleve, details_notes=details_notes, session_nom=session_nom))

@app.route("/login", methods=["GET", "POST"])
def route_login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM admin_account WHERE username = ?", (username,))
        admin_res = cursor.fetchone()
        if admin_res and check_password_hash(admin_res[0], password):
            session.clear()
            session["username"] = username
            session["user_role"] = "ADMIN"
            conn.close()
            return redirect(url_for("index"))
        cursor.execute("SELECT id_ens, password_hash FROM enseignants WHERE username = ?", (username,))
        prof_res = cursor.fetchone()
        if prof_res and prof_res[1] and check_password_hash(prof_res[1], password):
            session.clear()
            session["username"] = username
            session["user_role"] = "ENSEIGNANT"
            session["id_enseignant"] = prof_res[0]
            conn.close()
            return redirect(url_for("index"))
        conn.close()
        flash("Identifiants invalides.", "staff_err")
    return render_template_string(LOGIN_TEMPLATE)

@app.route("/logout")
def route_logout():
    session.clear()
    return redirect(url_for("route_login"))

@app.route("/")
def index():
    if not session.get("user_role") or session.get("user_role") == "ELEVE": return redirect(url_for("route_login"))
    
    id_sess_actuel, nom_sess_actuel = engine.obtenir_session_active()
    toutes_sessions = engine.obtenir_toutes_les_sessions()
    
    session_visualisee_id = request.args.get("session_visualisee_id")
    if not session_visualisee_id or not session_visualisee_id.isdigit():
        session_visualisee_id = id_sess_actuel
    else:
        session_visualisee_id = int(session_visualisee_id)
        
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("SELECT valeur FROM config_site WHERE cle = 'titre_onglets'")
    config_titre_res = cursor.fetchone()
    titre_onglets = config_titre_res[0] if config_titre_res else "📁 Choisissez l'onglet du mois / trimestre à consulter :"
    
    cursor.execute("SELECT nom_session, statut FROM sessions_eval WHERE id_session = ?", (session_visualisee_id,))
    infos_sess = cursor.fetchone()
    nom_session_visualisee = infos_sess[0] if infos_sess else nom_sess_actuel
    est_session_historique = (infos_sess[1] == 'ARCHIVEE') if infos_sess else False

    classe_id = request.args.get("classe_id")
    classes = cursor.execute("SELECT id_classe, nom_classe, niveau FROM classes").fetchall()
    t_classes = cursor.execute("SELECT COUNT(*) FROM classes").fetchone()[0]
    
    t_eleves = cursor.execute("SELECT COUNT(*) FROM eleves WHERE session_id = ?", (session_visualisee_id,)).fetchone()[0]
    notes_all = [r[0] for r in cursor.execute("SELECT n.note FROM notes n JOIN eleves e ON n.id_eleve = e.id_eleve WHERE e.session_id = ?", (session_visualisee_id,)).fetchall()]
    taux = round((sum(1 for n in notes_all if n >= 10) / len(notes_all)) * 100, 2) if notes_all else 0.0
    stats = {"total_classes": t_classes, "total_eleves": t_eleves, "taux_reussite": taux}
    
    classement_data, notes_classe, selected_id = [], [], None
    if classe_id and classe_id.isdigit():
        selected_id = int(classe_id)
        res, _ = engine.obtenir_classement_par_session(selected_id, session_visualisee_id)
        if res: classement_data = [(idx, item[0], item[1], item[2]) for idx, item in enumerate(res, 1)]
        
        if session.get('user_role') == 'ENSEIGNANT':
            cursor.execute("""
                SELECT n.id_note, (e.prenom || ' ' || e.nom), c.matiere, c.coefficient, n.note 
                FROM notes n JOIN eleves e ON n.id_eleve = e.id_eleve JOIN cours c ON n.id_cours = c.id_cours
                WHERE e.classe_id = ? AND c.id_enseignant = ? AND e.session_id = ?
            """, (selected_id, int(session.get('id_enseignant')), session_visualisee_id))
        else:
            cursor.execute("""
                SELECT n.id_note, (e.prenom || ' ' || e.nom), c.matiere, c.coefficient, n.note 
                FROM notes n JOIN eleves e ON n.id_eleve = e.id_eleve JOIN cours c ON n.id_cours = c.id_cours 
                WHERE e.classe_id = ? AND e.session_id = ?
            """, (selected_id, session_visualisee_id))
        notes_classe = cursor.fetchall()
        
    conn.close()
    return render_template_string(BASE_LAYOUT, MainContent=render_template_string(
        INDEX_CONTENT, classes=classes, stats=stats, classement=classement_data, 
        classe_selectionnee=selected_id, notes_classe=notes_classe, 
        toutes_sessions=toutes_sessions, session_visualisee_id=session_visualisee_id,
        nom_session_visualisee=nom_session_visualisee, est_session_historique=est_session_historique,
        titre_onglets=titre_onglets
    ))

@app.route("/modifier-titre-onglets", methods=["POST"])
def route_modifier_titre_onglets():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    nouveau_titre = request.form.get("nouveau_titre", "").strip()
    if nouveau_titre:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO config_site (cle, valeur) VALUES ('titre_onglets', ?)", (nouveau_titre,))
        conn.commit()
        conn.close()
        flash("Texte d'en-tête mis à jour !", "success")
    return redirect(url_for("index"))

@app.route("/renommer-session/<int:id_sess>", methods=["POST"])
def route_renommer_session(id_sess):
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    nouveau_nom = request.form.get("nouveau_nom", "").strip()
    if nouveau_nom:
        try:
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("UPDATE sessions_eval SET nom_session = ? WHERE id_session = ?", (nouveau_nom, id_sess))
            conn.commit()
            conn.close()
            flash(f"L'onglet a bien été renommé en '{nouveau_nom}'.", "success")
        except sqlite3.Error:
            flash("Erreur lors du renommage de l'onglet.", "error")
    return redirect(url_for("route_classes"))

@app.route("/supprimer-session/<int:id_sess>")
def route_supprimer_session(id_sess):
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM sessions_eval")
        if cursor.fetchone()[0] <= 1:
            flash("🛑 Impossible de supprimer cet onglet. Le système requiert au minimum une session active.", "warning")
            conn.close()
            return redirect(url_for("route_classes"))
            
        cursor.execute("DELETE FROM sessions_eval WHERE id_session = ?", (id_sess,))
        conn.commit()
        
        cursor.execute("SELECT COUNT(*) FROM sessions_eval WHERE statut = 'ACTIVE'")
        if cursor.fetchone()[0] == 0:
            cursor.execute("SELECT id_session FROM sessions_eval ORDER BY id_session DESC LIMIT 1")
            dernier_id = cursor.fetchone()[0]
            cursor.execute("UPDATE sessions_eval SET statut = 'ACTIVE' WHERE id_session = ?", (dernier_id,))
            conn.commit()
            
        conn.close()
        flash("L'onglet ainsi que l'ensemble de ses données d'évaluation ont été effacés.", "success")
    except sqlite3.Error:
        flash("Erreur technique lors de la suppression.", "error")
    return redirect(url_for("route_classes"))


@app.route("/bulletin/<int:id_eleve>")
def route_bulletin(id_eleve):
    if session.get("user_role") not in ["ADMIN", "ENSEIGNANT"]: return redirect(url_for("route_login"))
    session_id = request.args.get("session_id")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    if not session_id:
        cursor.execute("SELECT id_session FROM sessions_eval WHERE statut = 'ACTIVE'")
        session_id = cursor.fetchone()[0]
    
    cursor.execute("SELECT nom_session FROM sessions_eval WHERE id_session = ?", (int(session_id),))
    session_nom = cursor.fetchone()[0]

    cursor.execute("SELECT e.id_eleve, e.nom, e.prenom, e.date_naissance, e.classe_id, c.nom_classe FROM eleves e LEFT JOIN classes c ON e.classe_id = c.id_classe WHERE e.id_eleve = ?", (id_eleve,))
    eleve = cursor.fetchone()
    
    if session.get("user_role") == "ENSEIGNANT":
        cursor.execute("SELECT co.matiere, co.coefficient, n.note FROM notes n JOIN cours co ON n.id_cours = co.id_cours WHERE n.id_eleve = ? AND co.id_enseignant = ?", (id_eleve, int(session.get("id_enseignant"))))
    else:
        cursor.execute("SELECT co.matiere, co.coefficient, n.note FROM notes n JOIN cours co ON n.id_cours = co.id_cours WHERE n.id_eleve = ?", (id_eleve,))
    details_notes = cursor.fetchall()
    
    total_points, total_coefs = 0.0, 0
    for _, coef, note in details_notes:
        total_points += (note * coef)
        total_coefs += coef
    moyenne_generale = round(total_points / total_coefs, 2) if total_coefs > 0 else 0.0
    conn.close()
    return render_template_string(BASE_LAYOUT, MainContent=render_template_string(BULLETIN_CONTENT, eleve=eleve, details_notes=details_notes, moyenne_generale=moyenne_generale, session_nom=session_nom))

@app.route("/nouvelle-session", methods=["POST"])
def route_nouvelle_session():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    nom_session = request.form.get("nom_session", "").strip()
    if nom_session:
        succes, msg = engine.basculer_nouvelle_session(nom_session)
        flash(msg, "success" if succes else "error")
    return redirect(url_for("route_classes"))

@app.route("/classes")
def route_classes():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    classes = cursor.execute("SELECT id_classe, nom_classe, niveau FROM classes").fetchall()
    profs = cursor.execute("SELECT id_ens, nom, prenom, matiere, username FROM enseignants").fetchall()
    sessions = cursor.execute("SELECT id_session, nom_session, statut FROM sessions_eval ORDER BY id_session DESC").fetchall()
    conn.close()
    return render_template_string(BASE_LAYOUT, MainContent=render_template_string(CLASSES_CONTENT, classes=classes, profs=profs, sessions=sessions))

@app.route("/sauvegardes")
def route_sauvegardes():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    return render_template_string(BASE_LAYOUT, MainContent=SAUVEGARDES_CONTENT)

@app.route("/export/professeurs")
def export_professeurs():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    profs = cursor.execute("SELECT id_ens, nom, prenom, matiere, username FROM enseignants").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['ID_Enseignant', 'Nom', 'Prenom', 'Matiere', 'Identifiant'])
    writer.writerows(profs)
    response = make_response(output.getvalue().encode('utf-8-sig'))
    response.headers["Content-Disposition"] = "attachment; filename=sauvegarde_professeurs.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

@app.route("/export/eleves")
def export_eleves():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    eleves = cursor.execute("SELECT e.id_eleve, e.nom, e.prenom, e.date_naissance, c.nom_classe, s.nom_session FROM eleves e LEFT JOIN classes c ON e.classe_id = c.id_classe LEFT JOIN sessions_eval s ON e.session_id = s.id_session").fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['ID_Eleve', 'Nom', 'Prenom', 'Date_Naissance', 'Classe', 'Session'])
    writer.writerows(eleves)
    response = make_response(output.getvalue().encode('utf-8-sig'))
    response.headers["Content-Disposition"] = "attachment; filename=sauvegarde_eleves.csv"
    response.headers["Content-Type"] = "text/csv"
    return response

@app.route("/ajouter-prof", methods=["POST"])
def route_ajouter_prof():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    succes, msg = engine.ajouter_enseignant(request.form.get("nom"), request.form.get("prenom"), request.form.get("matiere"), request.form.get("username"), request.form.get("password"))
    flash(msg, "success" if succes else "error")
    return redirect(url_for("route_classes"))

@app.route("/supprimer-prof/<int:id_ens>")
def route_supprimer_prof(id_ens):
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    succes, msg = engine.supprimer_enseignant(id_ens)
    flash(msg, "success" if succes else "error")
    return redirect(url_for("route_classes"))

@app.route("/ajouter-classe", methods=["POST"])
def route_ajouter_classe():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    succes, msg = engine.ajouter_classe(request.form.get("nom_classe"), request.form.get("niveau"))
    flash(msg, "success" if succes else "error")
    return redirect(url_for("route_classes"))

@app.route("/supprimer-classe/<int:id_classe>")
def route_supprimer_classe(id_classe):
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    succes, msg = engine.supprimer_classe(id_classe)
    flash(msg, "success" if succes else "error")
    return redirect(url_for("route_classes"))

@app.route("/ajouter-cours", methods=["POST"])
def route_ajouter_cours():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    succes, msg = engine.ajouter_cours(request.form.get("id_classe"), request.form.get("id_enseignant"), request.form.get("matiere"), request.form.get("coefficient"))
    flash(msg, "success" if succes else "error")
    return redirect(url_for("route_classes"))

@app.route("/ajouter-eleve", methods=["GET", "POST"])
def route_ajouter_eleve():
    if session.get("user_role") != "ADMIN": return redirect(url_for("route_login"))
    if request.method == "POST":
        succes, msg = engine.ajouter_eleve(request.form.get("nom"), request.form.get("prenom"), request.form.get("date_naissance"), request.form.get("classe_id"))
        flash(msg, "success" if succes else "error")
        return redirect(url_for("index"))
    conn = sqlite3.connect(DB_NAME)
    classes = conn.cursor().execute("SELECT id_classe, nom_classe, niveau FROM classes").fetchall()
    conn.close()
    return render_template_string(BASE_LAYOUT, MainContent=render_template_string(FORM_ELEVE_CONTENT, classes=classes))

@app.route("/modifier-note/<int:id_note>", methods=["POST"])
def route_modifier_note(id_note):
    if session.get("user_role") not in ["ADMIN", "ENSEIGNANT"]: return redirect(url_for("route_login"))
    succes, msg = engine.modifier_note(id_note, request.form.get("nouvelle_note", 0), session.get("user_role"), session.get("id_enseignant"))
    flash(msg, "success" if succes else "error")
    return redirect(request.referrer or url_for("index"))

if __name__ == "__main__":
    init_db()
    webbrowser.open("http://127.0.0.1:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
# 1. Compte Administrateur (Direction)
#Identifiant : admin

#Mot de passe : admin123

#2. Comptes Enseignants (Professeurs)
#Professeur de Mathématiques (M. KONE Ibrahim) :
# Identifiant : prof_math
# Mot de passe : math123
# Professeur de Biologie (Mme SANOGO Awa) :
# Identifiant : prof_bio
# Mot de passe : bio123
# 3. Accès Élèves
# Pour l'espace élève, il n'y a pas de mot de passe. Il suffit d'entrer exactement le nom et le prénom d'un élève inscrit dans la base de données. Voici les trois élèves de démonstration :
# Nom : TRAORE | Prénom : Mariam
# Nom : DIARRA | Prénom : Adama
# Nom : COULIBALY | Prénom : Oumar