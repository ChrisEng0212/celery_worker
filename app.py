import os
from flask import Flask, flash, render_template, redirect, request
from tasks import runStream

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', "super-secret")


@app.route('/')
def main():
    return render_template('main.html')


@app.route('/add', methods=['POST'])
def add_inputs():
    x = int(request.form['x'] or 0)

    runStream.delay()

    flash("Your command has been submitted: " + str(x))
    return redirect('/')

