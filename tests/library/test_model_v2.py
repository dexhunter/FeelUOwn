import pickle
import subprocess
import sys

import pytest
import pydantic

from feeluown.library import SongModel, BriefAlbumModel, BriefArtistModel, BriefSongModel
from feeluown.utils.utils import elfhash


def test_create_song_model_basic():
    identifier = '1'
    brief_album = BriefAlbumModel(identifier='1', source='x',
                                  name='Film', artists_name='Audrey')
    brief_artist = BriefArtistModel(identifier='1', source='x', name='Audrey')
    # song should be created
    song = SongModel(identifier=identifier, source='x', title='Moon', album=brief_album,
                     artists=[brief_artist], duration=240000)
    # check song's attribute value
    assert song.artists_name == 'Audrey'

    # song model cache get/set
    song.cache_set('count', 1)
    assert song.cache_get('count') == (1, True)


def test_create_model_with_extra_field():
    with pytest.raises(pydantic.ValidationError):
        BriefSongModel(identifier=1, source='x', unk=0)


def test_song_model_is_hashable():
    """
    Song model must be hashable.
    """
    song = BriefSongModel(identifier=1, source='x')
    hash(song)


@pytest.mark.parametrize('field', ['source', 'identifier'])
def test_song_model_hash_after_identity_change(field):
    song = BriefSongModel(identifier='1', source='x')
    hash(song)
    setattr(song, field, '雪')
    fresh = BriefSongModel(identifier=song.identifier, source=song.source)
    assert hash(song) == hash(fresh)
    assert fresh in {song}


@pytest.mark.parametrize('field', ['source', 'identifier'])
def test_song_model_hash_with_custom_format(field):
    class FormattedString(str):
        suffix = ''

        def __format__(self, spec):
            return super().__format__(spec) + self.suffix

    song = BriefSongModel(identifier='1', source='x')
    value = FormattedString(getattr(song, field))
    setattr(song, field, value)
    hash(song)
    value.suffix = ':changed'
    expected = elfhash(f'{song.source}{song.identifier}'.encode()) * 1000
    assert hash(song) == expected + id(type(song)) % 1000


@pytest.mark.parametrize('protocol', [0, pickle.HIGHEST_PROTOCOL])
def test_song_model_hash_after_pickle_in_fresh_process(protocol):
    song = BriefSongModel(identifier='42', source='provider')
    hash(song)
    code = '''
import pickle
import sys
from feeluown.library import BriefSongModel

restored = pickle.loads(sys.stdin.buffer.read())
fresh = BriefSongModel(identifier='42', source='provider')
assert restored == fresh
assert hash(restored) == hash(fresh)
assert fresh in {restored}
assert {restored: 'present'}[fresh] == 'present'
'''
    subprocess.run([sys.executable, '-c', code],
                   input=pickle.dumps(song, protocol=protocol),
                   check=True, capture_output=True, timeout=30)
